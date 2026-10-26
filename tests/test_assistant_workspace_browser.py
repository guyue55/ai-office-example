"""新浏览器上下文验证真实同源BFF、共享侧栏与全页组件；不使用用户登录。"""

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright
from test_assistant_workspace_web import install_workspace
from test_web_console import web  # noqa: F401

pytestmark = pytest.mark.live


def test_global_assistant_sidebar_workspace_navigation_and_decision(web):  # noqa: F811
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，只启动隔离应用和浏览器")
    conversation, turn, calls, state = install_workspace(web)
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
        with sync_playwright() as p:
            browser = p.chromium.launch(
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
                page.goto(base + "/console/dashboard")
                page.get_by_role("button", name="✦ 智能助手", exact=True).click()
                expect(page.locator("#assistant-panel")).to_be_visible()
                expect(page.locator("#assistant-conversations option")).to_have_count(2)
                page.locator("#assistant-conversations").select_option(conversation["id"])
                expect(page.get_by_role("button", name="批准本次操作", exact=True)).to_be_visible()
                evidence = Path("runtime/agent-research/browser")
                evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(evidence / "assistant-sidebar.png"), full_page=True)
                # 非模态，主页面仍可操作；正文不成为HTML或脚本。
                assert page.locator("dialog[open]").count() == 0
                assert page.locator("#assistant-messages img").count() == 0
                assert page.locator("#assistant-messages script").count() == 0
                assert page.locator("body").evaluate("node => node.scrollWidth <= innerWidth")
                page.get_by_role("button", name="批准本次操作", exact=True).click()
                expect(page.locator("#action-confirm")).to_be_visible()
                page.locator("[data-confirm-cancel]").click()
                assert not any(name == "agent.decide" for name, _ in calls)
                # 两个页面先读取同一预览，然后验证重复决定不会变成第二次执行。
                other = context.new_page()
                other.goto(base + "/console/assistant/workspace")
                expect(other.locator("#assistant-conversations option")).to_have_count(2)
                other.locator("#assistant-conversations").select_option(conversation["id"])
                expect(other.get_by_role("button", name="批准本次操作", exact=True)).to_be_visible()
                other.get_by_role("button", name="批准本次操作", exact=True).click()
                expect(other.locator("#action-confirm")).to_be_visible()
                page.get_by_role("button", name="批准本次操作", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator("#assistant-feedback")).to_contain_text("任务正在处理")
                other.locator("[data-confirm-accept]").click()
                expect(other.locator("#assistant-feedback")).to_contain_text("状态已变化")
                assert turn["pending"]["decision"] is True
                # 关闭只改变视图，刷新／切页恢复同一会话ID。
                page.get_by_role("button", name="收起智能助手", exact=True).click()
                expect(page.locator("#assistant-panel")).to_be_hidden()
                expect(page.locator("#assistant-launch")).to_be_focused()
                page.reload()
                expect(page.locator("#assistant-panel")).to_be_hidden()
                page.locator("#assistant-launch").click()
                expect(page.locator("#assistant-conversations")).to_have_value(conversation["id"])
                page.locator(".assistant-tools a").click()
                expect(page).to_have_url(base + "/console/assistant/workspace")
                expect(page.locator("#assistant-panel")).to_have_class(
                    "assistant-panel assistant-full"
                )
                expect(page.locator("#assistant-conversations")).to_have_value(conversation["id"])
                page.set_viewport_size({"width": 1024, "height": 768})
                assert page.locator("body").evaluate("node => node.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator("body").evaluate("node => node.scrollWidth <= innerWidth")
                evidence = Path("runtime/agent-research/browser")
                evidence.mkdir(parents=True, exist_ok=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path=str(evidence / "assistant-workspace.png"), full_page=True)
                assert not any(name == "agent.cancel" for name, _ in calls)
                turn.update(
                    status="completed",
                    pending=None,
                    result={"explanation": "合成回复", "receipts": []},
                )
                page.get_by_role("button", name="重新加载", exact=True).click()
                expect(page.locator("#assistant-feedback")).to_contain_text("已同步")
                page.get_by_role("button", name="新会话", exact=True).click()
                page.locator("#assistant-input").fill("合成的第二个工作要求")
                page.get_by_role("button", name="发送", exact=True).click()
                expect(page.locator("#assistant-input")).to_have_value("")
                expect(page.locator("#assistant-messages")).to_contain_text("合成任务已结束")
                submitted = [payload for name, payload in calls if name == "agent.start"][-1]
                assert (
                    submitted["conversation_id"] == conversation["id"]
                    and submitted["context_page"] == "assistant"
                )
                assert "message_history" not in submitted
                turn.update(status="running", pending=None, result=None)
                page.get_by_role("button", name="重新加载", exact=True).click()
                expect(page.get_by_role("button", name="停止任务", exact=True)).to_be_visible()
                page.get_by_role("button", name="停止任务", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator("#assistant-feedback")).to_contain_text("已同步")
                assert any(name == "agent.cancel" for name, _ in calls)
                turn.update(status="running", cancel_requested=False)
                page.get_by_role("button", name="重新加载", exact=True).click()
                expect(page.locator("#assistant-feedback")).to_contain_text("任务正在处理")
                # 网络中断只影响读取，不能隐式重发决定／取消；恢复时重新取权威快照。
                decisions_before = len([name for name, _ in calls if name == "agent.decide"])
                page.route("**/console/assistant-ui/conversations/*", lambda route: route.abort())
                page.get_by_role("button", name="重新加载", exact=True).click()
                expect(page.locator("#assistant-feedback")).to_contain_text("暂时无法连接")
                assert (
                    len([name for name, _ in calls if name == "agent.decide"]) == decisions_before
                )
                page.unroute("**/console/assistant-ui/conversations/*")
                page.get_by_role("button", name="重新加载", exact=True).click()
                expect(page.locator("#assistant-feedback")).to_contain_text("任务正在处理")
                # 权限撤销清空已呈现正文，不留下旧消息与可批准按钮。
                state["allowed"] = False
                page.get_by_role("button", name="重新加载", exact=True).click()
                expect(page.locator("#assistant-messages")).to_contain_text("当前身份或权限不可用")
                expect(page.locator("#assistant-input")).to_be_disabled()
                assert "合成请求" not in page.locator("#assistant-messages").inner_text()
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
