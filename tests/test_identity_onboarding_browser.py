"""真实Web路由与共享弹窗的开通预览交互；身份及执行回执使用合成适配。"""

import os
import socket
import threading
import time

import pytest
import uvicorn
from app.entrypoints.web.responses import instance_cookie
from playwright.sync_api import expect, sync_playwright
from test_web_console import web  # noqa: F401

pytestmark = pytest.mark.live


def test_pending_dialog_previews_before_explicit_confirmation(request):
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需显式开启隔离浏览器")
    fixture = request.getfixturevalue("web")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(fixture.client.app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.02)
        assert server.started
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
            )
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies(
                [
                    {
                        "name": instance_cookie(
                            "office_web_session", fixture.application.settings.public_url
                        ),
                        "value": fixture.cookie,
                        "url": base,
                    }
                ]
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(base + "/console/employees/pending")
                trigger = page.get_by_text("核对并开通", exact=True)
                expect(trigger).to_be_visible()
                assert trigger.inner_text() == "核对并开通"
                trigger.click()
                panel = page.get_by_role("dialog", name="核对并开通", exact=True)
                expect(panel).to_be_visible()
                panel.locator('input[name="name"]').fill("合成人员")
                panel.get_by_role("button", name="下一步：核对开通", exact=True).click()
                expect(page.get_by_role("heading", name="核对外部身份开通")).to_be_visible()
                expect(page.locator("main")).to_contain_text("kc-subject-value")
                assert all(
                    call.args[1] != "access.change.apply"
                    for call in fixture.application.call.call_args_list
                )
                page.get_by_role("link", name="取消", exact=True).click()
                expect(page).to_have_url(base + "/console/employees/pending")
                assert all(
                    call.args[1] != "access.change.apply"
                    for call in fixture.application.call.call_args_list
                )
                page.goto(base + "/console/employees/external")
                page.locator('input[name="name"]').fill("外部合成人员")
                page.locator('input[name="subject"]').fill("synthetic-subject")
                page.get_by_role("button", name="下一步：核对开通", exact=True).click()
                expect(page.get_by_role("heading", name="核对外部身份开通")).to_be_visible()
                page.get_by_role("button", name="确认并开通", exact=True).click()
                expect(page).to_have_url(base + "/console/employees")
                assert (
                    sum(
                        call.args[1] == "access.change.apply"
                        for call in fixture.application.call.call_args_list
                    )
                    == 1
                )
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
