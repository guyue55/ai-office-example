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


def test_chat_composer_drafts_markdown_copy_and_keyed_reading(web):  # noqa: F811
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅启动隔离应用和浏览器")
    import copy

    conversation, turn, calls, state = install_workspace(web)
    original = web.application.call.side_effect
    turn.update(
        status="completed",
        pending=None,
        result={
            "explanation": "**合成说明**\n\n- 第一项\n\n|列|值|\n|--|--|\n|A|1|\n\n```python\nprint('x')\n```\n<img src=x onerror=evil()>\n[危险](javascript:alert(1))\n![追踪图](https://invalid.example/image)",
            "receipts": [],
        },
    )
    previous = copy.deepcopy(turn)
    previous.update(
        id="older-task",
        prompt="历史原文用于选择",
        result={"explanation": "历史回复", "receipts": []},
    )

    async def operation(principal, name, payload, trace, **options):
        if name == "agent.conversation.rename":
            if payload["version"] != conversation["version"]:
                from app.common.errors import DomainError

                raise DomainError(409, "constraint_conflict", "会话版本已变化")
            conversation.update(title=payload["title"], version=conversation["version"] + 1)
            calls.append((name, payload))
            return conversation
        if name == "agent.start" and state.get("delay"):
            import asyncio

            await asyncio.sleep(0.5)
        result = await original(principal, name, payload, trace, **options)
        if name == "agent.conversation.list":
            second = {**conversation, "id": "conversation-two", "title": "第二个会话"}
            result = {**result, "items": [conversation, second]}
        if name == "agent.conversation.get":
            result = {**result, "id": payload["identifier"], "turns": [previous, turn]}
        return result

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
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
            )
            try:
                context = browser.new_context(viewport={"width": 1600, "height": 1000})
                context.add_cookies(
                    [{"name": "office_web_session", "value": web.cookie, "url": base}]
                )
                # localhost可能有Clipboard API；明确模拟真实LAN HTTP的不支持情形。
                context.add_init_script(
                    "Object.defineProperty(navigator,'clipboard',{value:undefined});Object.defineProperty(crypto,'randomUUID',{value:undefined})"
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/console/dashboard")
                page.locator("#assistant-launch").click()
                select = page.locator("#assistant-conversations")
                expect(select.locator("option")).to_have_count(3)
                select.select_option(conversation["id"])
                expect(page.locator(".assistant-markdown strong")).to_have_text("合成说明")
                assert (
                    page.locator(
                        ".assistant-markdown img,.assistant-markdown script,.assistant-markdown iframe"
                    ).count()
                    == 0
                )
                assert page.locator(".assistant-markdown a[href^='javascript:']").count() == 0
                expect(page.locator(".assistant-markdown table")).to_be_visible()
                assert page.locator("body").evaluate("node=>node.scrollWidth<=innerWidth")
                page.get_by_role("button", name="复制回复", exact=True).last.click()
                expect(page.locator("#record-dialog")).to_be_visible()
                expect(page.get_by_label("待复制原文")).to_have_value(turn["result"]["explanation"])
                assert page.get_by_label("待复制原文").evaluate(
                    "node=>node.selectionEnd===node.value.length"
                )
                page.get_by_role("button", name="关闭详情", exact=True).click()
                field = page.locator("#assistant-input")
                field.fill("第一个会话的未发草稿")
                select.select_option("conversation-two")
                expect(field).to_have_value("")
                field.fill("第二份草稿")
                select.select_option(conversation["id"])
                expect(field).to_have_value("第一个会话的未发草稿")
                field.dispatch_event(
                    "keydown", {"key": "Enter", "ctrlKey": True, "isComposing": True}
                )
                assert not any(name == "agent.start" for name, _ in calls)
                field.fill("多行输入\n" * 20)
                assert 100 < field.evaluate("node=>node.getBoundingClientRect().height") <= 180
                # 未变化的旧轮保持同一个DOM节点；状态同步不破坏选择与历史位置。
                page.evaluate(
                    "window.prior=document.querySelector('[data-run=older-task]');const range=document.createRange();range.selectNodeContents(prior.querySelector('.assistant-user'));getSelection().removeAllRanges();getSelection().addRange(range)"
                )
                turn["version"] += 1
                turn["result"]["explanation"] += "\n新回执"
                page.get_by_role("button", name="重新加载", exact=True).click()
                expect(page.locator(".assistant-markdown").last).to_contain_text("新回执")
                assert page.evaluate(
                    "window.prior===document.querySelector('[data-run=older-task]')"
                )
                expect(
                    page.get_by_role("button", name="有更新 · 回到最新", exact=True)
                ).to_be_visible()
                page.get_by_role("button", name="有更新 · 回到最新", exact=True).click()
                expect(page.locator("[data-assistant-latest]")).to_be_hidden()
                # 键盘改变侧栏宽度，不占用主页面全部宽度。
                separator = page.get_by_role("separator", name="调整助手宽度")
                separator.focus()
                separator.press("ArrowLeft")
                expect(separator).to_have_attribute("aria-valuenow", "440")
                page.get_by_role("button", name="改名", exact=True).click()
                page.get_by_label("会话标题", exact=True).fill("重新命名的会话")
                page.get_by_role("button", name="保存标题", exact=True).click()
                expect(page.locator("#record-dialog")).to_be_hidden()
                assert conversation["title"] == "重新命名的会话"
                page.locator("#assistant-query").fill("合成%_")
                with page.expect_response(
                    lambda response: (
                        "assistant-ui/bootstrap?" in response.url and "q=" in response.url
                    )
                ) as search_response:
                    page.get_by_role("button", name="搜索", exact=True).click()
                assert search_response.value.status == 200
                page.wait_for_function(
                    "document.querySelector('#assistant-conversations').disabled===false"
                )
                assert [payload for name, payload in calls if name == "agent.conversation.list"][
                    -1
                ]["q"] == "合成%_"
                field.fill("")
                page.get_by_role("button", name="了解我的权限", exact=True).click()
                expect(field).to_have_value("请说明我当前具备哪些权限，以及可以进行哪些操作。")
                assert not any(name == "agent.start" for name, _ in calls)
                assert page.evaluate(
                    "Object.values(sessionStorage).every(value=>!value.includes('未发草稿')&&!value.includes('第二份草稿'))"
                )
                evidence = Path("runtime/agent-research/browser")
                evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(evidence / "assistant-chat-ux.png"), full_page=True)
                # 回执未到时继续起草，受理后不能把新草稿清空；不依赖HTTP不可用的randomUUID。
                state["delay"] = True
                with page.expect_request(
                    lambda req: (
                        req.url.endswith("assistant-ui/action")
                        and '"start"' in (req.post_data or "")
                    )
                ):
                    page.get_by_role("button", name="发送", exact=True).click()
                field.fill("等待受理时新写的草稿")
                expect(page.locator("#assistant-messages")).to_contain_text("合成任务已结束")
                expect(field).to_have_value("等待受理时新写的草稿")
                assert len([name for name, _ in calls if name == "agent.start"]) == 1
                assert page.url == base + "/console/dashboard"
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
