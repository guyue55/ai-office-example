"""临时PG、Keycloak、真实OIDC与后台执行器；仅使用本轮合成账号。"""

import asyncio
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import browser_errors
import httpx
import pytest
import uvicorn
from app.infrastructure.config import IdentityManagement
from app.infrastructure.database import async_sessions
from app.infrastructure.models import IdentityLink
from app.main import create_app
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select
from test_identity_admin_isolated import isolated_identity  # noqa: F401
from test_query_postgres import isolated_query_db  # noqa: F401

pytestmark = pytest.mark.live


@pytest.fixture
def oidc_accounts(request, tmp_path, monkeypatch):
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅使用独立浏览器与合成账号")
    original = request.getfixturevalue("isolated_query_db")
    issuer, management_secret_file = request.getfixturevalue("isolated_identity")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from scripts.identity_clients import ensure_client

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    secret_file = tmp_path / "web-secret"
    secret_file.write_text(secrets.token_urlsafe(48))
    secret_file.chmod(0o600)
    settings = original.model_copy(
        update={
            "issuer": issuer,
            "audience": base + "/mcp",
            "public_url": base,
            "web_client_id": "isolated-web",
            "web_session_secret_file": secret_file,
            "identity_management": IdentityManagement(
                client_id="management", client_secret_file=management_secret_file
            ),
        }
    )
    # 仅消费本轮夹具自己的初始化秘密；不读取现有实例配置。
    setup_secret = json.loads((tmp_path / "realm.json").read_text())["clients"][0]["secret"]
    operator_password = secrets.token_urlsafe(24) + "Aa1!"
    admin_base = issuer.replace("/realms/", "/admin/realms/")
    with httpx.Client(timeout=10) as http:
        response = http.post(
            issuer + "/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "setup",
                "client_secret": setup_secret,
            },
        )
        if response.status_code != 200:
            raise RuntimeError("隔离身份初始化授权失败")
        http.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        response = http.post(
            admin_base + "/client-scopes",
            json={
                "name": "office",
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "true"},
            },
        )
        response.raise_for_status()
        response = http.get(admin_base + "/client-scopes")
        response.raise_for_status()
        scope_id = next(row["id"] for row in response.json() if row["name"] == "office")
        ensure_client(
            http,
            admin_base,
            scope_id,
            settings.web_client_id,
            base + "/auth/callback",
            settings.audience,
        )
        response = http.post(
            admin_base + "/users",
            json={
                "username": "isolated-admin",
                "firstName": "隔离管理员",
                "enabled": True,
                "credentials": [
                    {"type": "password", "value": operator_password, "temporary": False}
                ],
            },
        )
        response.raise_for_status()
        response = http.get(
            admin_base + "/users", params={"username": "isolated-admin", "exact": "true"}
        )
        response.raise_for_status()
        subject = response.json()[0]["id"]

    async def bind_admin():
        factory = async_sessions(settings)
        try:
            async with factory.begin() as db:
                link = await db.scalar(
                    select(IdentityLink).where(
                        IdentityLink.issuer == original.issuer, IdentityLink.subject == "operator"
                    )
                )
                link.issuer, link.subject = issuer, subject
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(bind_admin())
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "隔离应用未完成生命周期启动"
        yield SimpleNamespace(base=base, password=operator_password, app=app)
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        listener.close()
        secret_file.unlink(missing_ok=True)
        assert not thread.is_alive(), "隔离应用未完成关闭"


def login(page, username, password):
    """只填写测试自建账号；不输出密码、不保存浏览器追踪或凭证截图。"""
    expect(page.locator("#password")).to_be_visible(timeout=15000)
    if page.locator("#username").count():
        page.locator("#username").fill(username)
    page.locator("#password").fill(password)
    page.locator("#kc-login").click()


def test_oidc_create_credential_first_login_and_disable(oidc_accounts):
    base = oidc_accounts.base
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
        )
        try:
            admin = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = admin.new_page()
            errors = browser_errors.collect(page)
            page.goto(base + "/console")
            expect(page.locator("#username")).to_be_visible(timeout=15000)
            assert (
                page.locator(".pf-v5-c-login__main").evaluate("n=>getComputedStyle(n).borderRadius")
                == "16px"
            )
            # 合成错误账号验证错误状态；不访问在用账号，也不记录凭证截图。
            login(page, "isolated-missing-user", secrets.token_urlsafe(24))
            expect(page.locator("#username")).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#input-error-username")).to_be_visible()
            assert page.locator("html").evaluate("n=>n.scrollWidth<=innerWidth")
            login(page, "isolated-admin", oidc_accounts.password)
            expect(page).to_have_url(base + "/console/dashboard", timeout=20000)
            page.goto(base + "/console/accounts/new")
            username = "browser-" + uuid4().hex[:12]
            page.locator('[name="name"]').fill("浏览器闭环员工")
            page.locator('[name="username"]').fill(username)
            page.get_by_role("button", name="核对开通信息", exact=True).click()
            expect(page.get_by_role("heading", name="核对账号操作", exact=True)).to_be_visible()
            page.get_by_role("button", name="确认并提交", exact=True).click()
            expect(page.locator('[data-receipt-state="awaiting_user"]')).to_be_visible(
                timeout=30000
            )
            operation_url = page.url
            csrf = page.locator('form[action$="/verify"] [name="csrf"]').input_value()
            # 未再次认证时，直接请求凭证必须拒绝，不能仅靠按钮隐藏。
            denied = admin.request.post(
                operation_url + "/credential", form={"csrf": csrf, "version": "1"}
            )
            assert denied.status == 403
            page.get_by_role("button", name="验证本人身份", exact=True).click()
            login(page, "isolated-admin", oidc_accounts.password)
            expect(page.get_by_role("button", name="生成并显示一次", exact=True)).to_be_visible(
                timeout=20000
            )
            page.get_by_role("button", name="生成并显示一次", exact=True).click()
            expect(page.get_by_role("heading", name="临时密码已准备", exact=True)).to_be_visible()
            temporary_password = page.locator("#temporary-password").input_value()
            expect(page.locator("#temporary-password")).to_have_attribute("type", "password")
            page.get_by_role("link", name="查看开通结果", exact=True).click()
            expect(page.locator('[data-receipt-state="succeeded"]')).to_be_visible(timeout=30000)
            detail_url = page.get_by_role("link", name="查看账号详情", exact=True).get_attribute(
                "href"
            )
            employee = browser.new_context()
            member = employee.new_page()
            member.on("pageerror", lambda error: errors.append(str(error)))
            member.goto(base + "/console")
            login(member, username, temporary_password)
            del temporary_password
            expect(member.locator("#password-new")).to_be_visible(timeout=15000)
            member.set_viewport_size({"width": 320, "height": 640})
            assert member.locator("html").evaluate("n=>n.scrollWidth<=innerWidth")
            member.locator('#kc-passwd-update-form [type="submit"]').scroll_into_view_if_needed()
            expect(member.locator('#kc-passwd-update-form [type="submit"]')).to_be_in_viewport()
            replacement = secrets.token_urlsafe(24) + "Aa1!"
            member.locator("#password-new").fill(replacement)
            member.locator("#password-confirm").fill(replacement)
            member.locator('#kc-passwd-update-form [type="submit"]').click()
            expect(member).to_have_url(base + "/console/dashboard", timeout=20000)
            response = employee.request.get(base + "/console/accounts/new")
            assert response.status == 403
            page.goto(base + detail_url)
            page.get_by_role("button", name="停用账号", exact=True).click()
            expect(page.get_by_role("heading", name="核对账号操作", exact=True)).to_be_visible()
            page.get_by_role("button", name="确认并提交", exact=True).click()
            expect(page.locator('[data-receipt-state="succeeded"]')).to_be_visible(timeout=30000)
            response = employee.request.get(base + "/console/dashboard", max_redirects=0)
            assert response.status == 403 or response.status in {301, 302, 303, 307, 308}
            if response.status != 403:
                assert response.headers.get("location", "") in {
                    "/console"
                } or "/realms/" in response.headers.get("location", "")
            page.goto(base + detail_url)
            page.get_by_role("button", name="启用账号", exact=True).click()
            page.get_by_role("button", name="确认并提交", exact=True).click()
            expect(page.locator('[data-receipt-state="succeeded"]')).to_be_visible(timeout=30000)
            # 新浏览器重新登录恢复访问；先前员工会话不得因此复活。
            response = employee.request.get(base + "/console/dashboard", max_redirects=0)
            assert response.status == 403 or response.status in {301, 302, 303, 307, 308}
            if response.status != 403:
                assert response.headers.get("location", "") in {
                    "/console"
                } or "/realms/" in response.headers.get("location", "")
            fresh = browser.new_context()
            returning = fresh.new_page()
            returning.goto(base + "/console")
            login(returning, username, replacement)
            expect(returning).to_have_url(base + "/console/dashboard", timeout=20000)
            assert errors == []
        finally:
            browser.close()
