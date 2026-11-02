"""隔离浏览器到真实账号Web入口；应用能力替身，不创建任何真实身份或凭证。"""

import os
import socket
import threading
import time
from datetime import UTC, datetime

import pytest
import uvicorn
from app.common.errors import DomainError
from app.entrypoints.web.responses import instance_cookie
from playwright.sync_api import expect, sync_playwright
from test_web_console import web  # noqa: F401

pytestmark = pytest.mark.live


def test_new_employee_validation_preview_and_confirm(web):  # noqa: F811
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅隔离应用／身份替身")
    original = web.application.call.side_effect
    calls = []
    selected = ["outside"]
    grants = []

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {
                "name": "隔离管理员",
                "permissions": ["identity.create", "identity.read", "access.manage", "access.read"],
            }
        if name == "account.capabilities":
            return {"enabled": True, "email_enabled": False}
        if name == "account.search":
            return {
                "notice": "仅隔离账号资料",
                "items": [
                    {
                        "id": "remote-person",
                        "name": "待关联人员",
                        "username": "existing01",
                        "email": "",
                        "enabled": True,
                        "linked_employee_id": None,
                    }
                ],
            }
        if name == "access.list" and payload["kind"] == "roles":
            return {
                "items": [
                    {
                        "id": "new-role",
                        "name": "新增查阅角色",
                        "permission_count": 2,
                        "employee_count": 0,
                    }
                ],
                "next": None,
                "has_more": False,
            }
        if name == "access.links.get":
            return {"id": "person", "version": 2, "ids": list(selected)}
        if name == "access.links.replace":
            grants.append(payload)
            selected[:] = payload["body"]["ids"]
            return {"id": "person", "version": 3}
        if name == "account.preview":
            if payload["request"].get("username") == "taken01":
                raise DomainError(409, "constraint_conflict", "账号信息已变更，请调整后核对")
            calls.append((name, payload))
            return {
                "request": payload["request"],
                "confirmation_digest": "test-digest",
                "effects": ["创建测试登录账号", "关联普通员工角色"],
            }
        if name == "account.submit":
            calls.append((name, payload))
            return {"next_url": "/console/accounts/operations/test-operation"}
        if name == "account.operation":
            return {
                "id": "test-operation",
                "version": 1,
                "username": "person01",
                "status": "succeeded",
                "message": "隔离操作已完成",
                "updated_at": datetime.now(UTC),
                "next_url": "/console/accounts/operations/test-operation",
                "employee_id": None,
            }
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(web.app, log_level="critical", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.02)
        assert server.started
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
            )
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies(
                    [
                        {
                            "name": instance_cookie(
                                "office_web_session", web.application.settings.public_url
                            ),
                            "value": web.cookie,
                            "url": base,
                        }
                    ]
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/console/accounts/new")
                expect(page.locator('[name="default_role"]')).to_be_checked()
                page.locator('[name="name"]').fill("隔离测试人员")
                page.locator('[name="username"]').fill("无效 账号")
                page.get_by_role("button", name="核对开通信息").click()
                expect(page.locator(".form-errors")).to_be_visible()
                expect(page.locator('[name="name"]')).to_have_value("隔离测试人员")
                assert not calls
                page.locator('[name="username"]').fill("person01")
                page.get_by_role("button", name="核对开通信息").click()
                expect(page.get_by_role("heading", name="核对账号操作")).to_be_visible()
                assert len(calls) == 1 and calls[0][0] == "account.preview"
                page.get_by_role("button", name="确认并提交", exact=True).click()
                expect(page.get_by_role("heading", name="账号处理进度")).to_be_visible()
                expect(page.locator("main")).to_contain_text("隔离操作已完成")
                assert calls[1][0] == "account.submit"
                assert calls[1][1]["confirmation_digest"] == "test-digest"
                assert calls[1][1]["idempotency_key"]
                assert calls[1][1]["request"] == calls[0][1]["request"]
                # 关联已有账号仍先预览；不按显示姓名自动绑定。
                page.goto(base + "/console/accounts/link?q=existing")
                expect(page.locator('[name="default_role"]')).to_be_checked()
                assert len(calls) == 2
                page.get_by_role("button", name="核对关联", exact=True).click()
                expect(page.get_by_role("heading", name="核对账号操作")).to_be_visible()
                assert calls[2][1]["request"]["kind"] == "link"
                assert calls[2][1]["request"]["remote_id"] == "remote-person"
                page.get_by_role("button", name="确认并提交", exact=True).click()
                expect(page.get_by_role("heading", name="账号处理进度")).to_be_visible()
                assert calls[3][0] == "account.submit"
                assert calls[3][1]["request"] == calls[2][1]["request"]
                # 授权编辑保留筛选外关联；放弃保护不应吞掉当前选择。
                page.goto(base + "/console/links/employees/person/roles")
                choice = page.locator('[name="id_new-role"]')
                choice.check()
                expect(page.locator("#grant-status")).to_contain_text("新增 1 项")
                page.locator('[name="q"]').fill("其他条件")
                page.get_by_role("button", name="搜索", exact=True).click()
                expect(page.locator("#action-confirm")).to_be_visible()
                page.locator("[data-confirm-cancel]").click()
                expect(choice).to_be_checked()
                assert not grants
                page.get_by_role("button", name="保存授权", exact=True).click()
                expect(page.locator("#ui-notifications")).to_contain_text("授权配置已保存")
                assert len(grants) == 1
                assert set(grants[0]["body"]["ids"]) == {"outside", "new-role"}
                assert grants[0]["body"]["version"] == 2
                # 服务端拒绝预览也留在原表单，取消的默认授权选择不能被补回。
                page.goto(base + "/console/accounts/new")
                page.locator('[name="name"]').fill("保留已填姓名")
                page.locator('[name="username"]').fill("taken01")
                page.locator('[name="default_role"]').uncheck()
                page.get_by_role("button", name="核对开通信息").click()
                expect(page.locator("[data-server-form-error]")).to_be_focused()
                expect(page.locator('[name="name"]')).to_have_value("保留已填姓名")
                expect(page.locator('[name="username"]')).to_have_value("taken01")
                expect(page.locator('[name="default_role"]')).not_to_be_checked()
                assert len(calls) == 4
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
