"""隔离浏览器到真实账号Web入口；应用能力替身，不创建任何真实身份或凭证。"""

import os
import socket
import threading
import time
from datetime import UTC, datetime

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright
from test_web_console import web  # noqa: F401

pytestmark = pytest.mark.live


def test_new_employee_validation_preview_and_confirm(web):  # noqa: F811
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅隔离应用／身份替身")
    original = web.application.call.side_effect
    calls = []

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {
                "name": "隔离管理员",
                "permissions": ["identity.create", "identity.read", "access.manage", "access.read"],
            }
        if name == "account.capabilities":
            return {"enabled": True, "email_enabled": False}
        if name == "account.preview":
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
                    [{"name": "office_web_session", "value": web.cookie, "url": base}]
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
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
