"""普通人员界面只保留统一开户流程；合成会话覆盖有／无脚本页面。"""

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


def test_personnel_hides_legacy_binding_with_and_without_scripts(request):
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
            errors = []
            try:
                for javascript in (True, False):
                    check_context = browser.new_context(java_script_enabled=javascript)
                    check_context.add_cookies(context.cookies())
                    check = check_context.new_page()
                    check.on("pageerror", lambda error: errors.append(str(error)))
                    check.goto(base + "/console/employees")
                    expect(
                        check.get_by_role("heading", name="人员管理", exact=True)
                    ).to_be_visible()
                    expect(check.locator('a[href="/console/employees/external"]')).to_have_count(0)
                    expect(check.locator('a[href="/console/employees/pending"]')).to_have_count(0)
                    expect(check.locator('input[name="subject"]')).to_have_count(0)
                    expect(check.locator("main")).not_to_contain_text("未开通访问记录")
                    check_context.close()
                assert all(
                    call.args[1] not in {"access.identity.pending", "access.change.apply"}
                    for call in fixture.application.call.call_args_list
                )
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
