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


def history(page):
    panel = page.locator("#assistant-history-panel")
    if not panel.is_visible():
        page.get_by_role("button", name="历史会话", exact=True).click()
    expect(panel).to_be_visible()
    return panel


def select_conversation(page, identifier):
    panel = history(page)
    panel.locator(f'[data-conversation="{identifier}"]').click()
    expect(panel).to_be_hidden()
    # 浮层关闭和草稿恢复是同步行为；正文仍需等待该会话的BFF响应。
    expect(page.locator("#assistant-topic")).not_to_have_text("正在加载会话")


def option(page, name):
    page.get_by_role("button", name="更多会话操作", exact=True).click()
    return page.locator("#assistant-options-panel").get_by_role("button", name=name, exact=True)


def sync(page):
    option(page, "同步会话状态").click()


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
                page.get_by_role("button", name="打开智能助手", exact=True).click()
                expect(page.locator("#assistant-panel")).to_be_visible()
                expect(history(page).locator("[data-conversation]")).to_have_count(1)
                select_conversation(page, conversation["id"])
                expect(page.get_by_role("button", name="批准本次操作", exact=True)).to_be_visible()
                evidence = Path("runtime/agent-research/browser")
                evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(evidence / "assistant-sidebar.png"), full_page=True)
                # 非模态，主页面仍可操作；正文不成为HTML或脚本。
                assert page.locator("dialog[open]").count() == 0
                assert page.locator("#assistant-messages img").count() == 0
                assert page.locator("#assistant-messages script").count() == 0
                assert page.locator("body").evaluate("node => node.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 390, "height": 1000})
                expect(page.locator("#assistant-panel")).to_have_attribute("aria-modal", "true")
                page.get_by_role("button", name="批准本次操作", exact=True).click()
                expect(page.locator("#action-confirm")).to_be_visible()
                page.locator("[data-confirm-cancel]").click()
                assert not any(name == "agent.decide" for name, _ in calls)
                page.set_viewport_size({"width": 1440, "height": 1000})
                expect(page.locator("main")).to_have_js_property("inert", False)
                # 两个页面先读取同一预览，然后验证重复决定不会变成第二次执行。
                other = context.new_page()
                other.goto(base + "/console/assistant/workspace")
                expect(history(other).locator("[data-conversation]")).to_have_count(1)
                select_conversation(other, conversation["id"])
                expect(other.get_by_role("button", name="批准本次操作", exact=True)).to_be_visible()
                other.get_by_role("button", name="批准本次操作", exact=True).click()
                expect(other.locator("#action-confirm")).to_be_visible()
                page.get_by_role("button", name="批准本次操作", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator("#assistant-feedback")).to_contain_text("决定已记录")
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
                expect(page.locator("#assistant-topic")).to_have_text(conversation["title"])
                expect(page.locator('[data-run="task-one"]')).to_be_visible()
                expect(page.locator("#assistant-messages > .empty")).to_have_count(0)
                # 业务页入口固定在右下角；窄屏打开同一侧栏，不再跳转工作区。
                page.get_by_role("button", name="收起智能助手", exact=True).click()
                launcher = page.locator("#assistant-launch")
                assert (
                    launcher.evaluate("el => getComputedStyle(el.parentElement).position")
                    == "fixed"
                )
                for viewport in [{"width": 1024, "height": 800}, {"width": 390, "height": 700}]:
                    page.set_viewport_size(viewport)
                    bounds = launcher.bounding_box()
                    assert bounds and bounds["y"] + bounds["height"] <= viewport["height"]
                    assert bounds["x"] + bounds["width"] <= viewport["width"]
                    launcher.click()
                    expect(page).to_have_url(base + "/console/dashboard")
                    expect(page.locator("#assistant-panel")).to_be_visible()
                    expect(page.locator("[data-assistant-close]")).to_be_visible()
                    page.get_by_role("button", name="收起智能助手", exact=True).click()
                    expect(launcher).to_be_focused()
                page.set_viewport_size({"width": 1440, "height": 1000})
                launcher.click()
                page.get_by_role("button", name="更多会话操作", exact=True).click()
                page.get_by_role("link", name="展开工作区", exact=True).click()
                expect(page).to_have_url(base + "/console/assistant/workspace")
                expect(page.locator("#assistant-panel")).to_have_class(
                    "assistant-panel assistant-full"
                )
                expect(page.locator("#assistant-topic")).to_have_text(conversation["title"])
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
                sync(page)
                expect(page.locator("#assistant-feedback")).to_contain_text("任务已完成")
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
                sync(page)
                expect(page.get_by_role("button", name="停止任务", exact=True)).to_be_visible()
                page.get_by_role("button", name="停止任务", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator("#assistant-feedback")).to_contain_text("任务已停止")
                assert any(name == "agent.cancel" for name, _ in calls)
                turn.update(status="running", cancel_requested=False)
                sync(page)
                expect(page.locator("#assistant-feedback")).to_contain_text("任务正在处理")
                # 网络中断只影响读取，不能隐式重发决定／取消；恢复时重新取权威快照。
                decisions_before = len([name for name, _ in calls if name == "agent.decide"])
                page.route("**/console/assistant-ui/conversations/*", lambda route: route.abort())
                sync(page)
                expect(page.locator("#assistant-feedback")).to_contain_text("暂时无法连接")
                assert (
                    len([name for name, _ in calls if name == "agent.decide"]) == decisions_before
                )
                page.unroute("**/console/assistant-ui/conversations/*")
                sync(page)
                expect(page.locator("#assistant-feedback")).to_contain_text("任务正在处理")
                # 权限撤销清空已呈现正文，不留下旧消息与可批准按钮。
                state["allowed"] = False
                sync(page)
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
                # 浮层Esc只关闭当前层，并把焦点还给触发器，不收起整个助手。
                history(page)
                expect(page.locator("#assistant-query")).to_be_focused()
                page.keyboard.press("Escape")
                expect(page.locator("#assistant-history-panel")).to_be_hidden()
                expect(page.locator("[data-assistant-history]")).to_be_focused()
                expect(page.locator("#assistant-panel")).to_be_visible()
                page.get_by_role("button", name="更多会话操作", exact=True).focus()
                expect(page.get_by_role("tooltip")).to_have_text("更多会话操作")
                page.keyboard.press("Escape")
                expect(page.get_by_role("tooltip")).to_be_hidden()
                page.evaluate(
                    "dispatchEvent(new Event('scroll'));dispatchEvent(new Event('resize'))"
                )
                page.wait_for_timeout(220)  # 超过提示150ms延迟，证明事件不会让提示复活。
                expect(page.get_by_role("tooltip")).to_be_hidden()
                menu_trigger = page.get_by_role("button", name="更多会话操作", exact=True)
                menu_trigger.press("Enter")
                width_picker = page.get_by_label("侧栏宽度", exact=True)
                expect(width_picker).to_be_focused()
                expect(page.get_by_role("button", name="重命名会话", exact=True)).to_be_disabled()
                width_picker.press("Home")
                width_picker.press("ArrowDown")
                expect(width_picker).to_have_value("420")
                # 可样式化原生select用Enter提交选项，再Tab离开选择器。
                if width_picker.evaluate("node=>node.matches(':open')"):
                    page.keyboard.press("Enter")
                page.keyboard.press("Tab")
                expect(page.get_by_role("link", name="展开工作区", exact=True)).to_be_focused()
                page.keyboard.press("Shift+Tab")
                expect(width_picker).to_be_focused()
                page.keyboard.press("Escape")
                expect(page.locator("#assistant-options-panel")).to_be_hidden()
                expect(menu_trigger).to_be_focused()
                expect(page.get_by_role("tooltip")).to_have_text("更多会话操作")
                page.keyboard.press("Escape")
                # 共享组件的无有效控件／无效初始目标回退，不依赖助手具体菜单。
                page.evaluate("""() => {
                    const trigger=document.createElement('button');trigger.id='focus-probe-trigger';trigger.textContent='测试浮层';
                    const panel=document.createElement('section');panel.id='focus-probe-panel';panel.setAttribute('popover','manual');
                    const hidden=document.createElement('button');hidden.hidden=true;
                    const disabled=document.createElement('button');disabled.disabled=true;panel.append(hidden,disabled);
                    document.body.append(trigger,panel);
                    window.focusProbe=OfficeUI.floating(trigger,panel,{initialFocus:()=>trigger});focusProbe.show();
                }""")
                expect(page.locator("#focus-probe-panel")).to_be_focused()
                page.keyboard.press("Escape")
                expect(page.locator("#focus-probe-trigger")).to_be_focused()
                page.evaluate(
                    "document.querySelector('#focus-probe-panel').remove();document.querySelector('#focus-probe-trigger').remove()"
                )
                # 在常用侧栏尺寸中正文保留至少65%，紧凑头部不挤占阅读区。
                page.set_viewport_size({"width": 1440, "height": 900})
                assert (
                    page.locator(".assistant-message-area").evaluate(
                        "node=>node.getBoundingClientRect().height / document.querySelector('#assistant-panel').getBoundingClientRect().height"
                    )
                    >= 0.65
                )
                expect(history(page).locator("[data-conversation]")).to_have_count(2)
                select_conversation(page, conversation["id"])
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
                # 组件级展示验证：普通读回执折叠，缺数与写入事实始终可见。
                page.evaluate("""() => {
                    const sample = OfficeAssistant.view.renderTurn({id:'visibility-probe',prompt:'回执可见性核验',status:'outcome_unknown',result:{explanation:'核对实际回执',receipts:[
                        {status:'read',operation:'projects',result:{complete:false,next:'page-two',items:[]}},
                        {status:'received',operation:'submit_record',result:{id:'visible-write',status:'candidate'}}
                    ]}}, {pageName:()=>'',showDetails:()=>{},refreshBusiness:()=>{}});
                    document.querySelector('#assistant-messages').append(sample);
                }""")
                probe = page.locator('[data-run="visibility-probe"]')
                expect(probe.locator(".assistant-receipt-group")).not_to_have_attribute("open", "")
                expect(
                    probe.get_by_text("结果覆盖不完整，请核对范围与缺失信息。", exact=True)
                ).to_be_visible()
                expect(probe.get_by_text("visible-write", exact=True)).to_be_visible()
                expect(
                    probe.get_by_text(
                        "写入结果待核实，请先检查实际数据，不要重复提交。", exact=True
                    )
                ).to_be_visible()
                assert probe.get_by_role("button", name="重试").count() == 0
                probe.evaluate("node=>node.remove()")
                page.get_by_role("button", name="复制回复", exact=True).last.click()
                expect(page.locator("#record-dialog")).to_be_visible()
                expect(page.get_by_label("待复制原文")).to_have_value(turn["result"]["explanation"])
                assert page.get_by_label("待复制原文").evaluate(
                    "node=>node.selectionEnd===node.value.length"
                )
                page.get_by_role("button", name="关闭详情", exact=True).click()
                # 状态通知使用当前任务权威状态；重复同步不重新写live region。
                page.evaluate("""() => {
                    window.feedbackWrites=0;
                    new MutationObserver(()=>window.feedbackWrites++).observe(document.querySelector('#assistant-feedback'),{childList:true,characterData:true,subtree:true});
                }""")
                turn.update(status="running", write_inflight=True)
                sync(page)
                expect(page.locator("#assistant-feedback")).to_contain_text("正在执行已批准操作")
                turn["cancel_requested"] = True
                sync(page)
                expect(page.locator("#assistant-feedback")).to_contain_text("已请求停止")
                turn.update(write_inflight=False, cancel_requested=False)
                for status, text in (
                    ("queued", "任务已排队"),
                    ("running", "任务正在处理"),
                    ("failed", "本轮未完成"),
                    ("interrupted", "任务已中断"),
                    ("cancelled", "任务已停止"),
                    ("outcome_unknown", "写入结果待核实"),
                    ("completed", "任务已完成"),
                ):
                    turn.update(status=status, version=turn["version"] + 1)
                    sync(page)
                    expect(page.locator("#assistant-feedback")).to_contain_text(text)
                    before = page.evaluate("feedbackWrites")
                    sync(page)
                    page.wait_for_function(
                        "document.querySelector('#assistant-search button').disabled===false"
                    )
                    assert page.evaluate("feedbackWrites") == before
                turn["result"]["outcome_unknown"] = True
                sync(page)
                expect(page.locator("#assistant-feedback")).to_contain_text("写入结果待核实")
                del turn["result"]["outcome_unknown"]
                sync(page)
                expect(page.locator("#assistant-feedback")).to_contain_text("任务已完成")
                previous["status"] = "failed"
                turn["id"] = "task-two"
                before = page.evaluate("feedbackWrites")
                sync(page)
                expect(page.locator("[data-run=task-two]")).to_be_attached()
                expect(page.locator("#assistant-feedback")).to_contain_text("任务已完成")
                assert page.evaluate("feedbackWrites") > before
                previous["status"] = "completed"
                turn["id"] = "task-one"
                select_conversation(page, "conversation-two")
                select_conversation(page, conversation["id"])
                field = page.locator("#assistant-input")
                field.fill("第一个会话的未发草稿")
                select_conversation(page, "conversation-two")
                expect(field).to_have_value("")
                field.fill("第二份草稿")
                select_conversation(page, conversation["id"])
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
                sync(page)
                expect(page.locator(".assistant-markdown").last).to_contain_text("新回执")
                assert page.evaluate(
                    "window.prior===document.querySelector('[data-run=older-task]')"
                )
                expect(page.get_by_role("button", name="回到最新消息", exact=True)).to_be_visible()
                page.get_by_role("button", name="回到最新消息", exact=True).click()
                expect(page.locator("[data-assistant-latest]")).to_be_hidden()
                # 键盘改变侧栏宽度，不占用主页面全部宽度。
                separator = page.get_by_role("separator", name="调整助手宽度")
                page.get_by_role("button", name="更多会话操作", exact=True).click()
                width_choice = page.get_by_label("侧栏宽度", exact=True)
                for choice in ("360", "640", "420"):
                    width_choice.select_option(choice)
                    expect(separator).to_have_attribute("aria-valuenow", choice)
                    assert page.locator("#assistant-panel").evaluate(
                        "node=>node.getBoundingClientRect().width"
                    ) == int(choice)
                page.keyboard.press("Escape")
                separator.focus()
                separator.press("ArrowLeft")
                expect(separator).to_have_attribute("aria-valuenow", "440")
                page.get_by_role("button", name="更多会话操作", exact=True).click()
                expect(width_choice).to_have_value("")
                expect(width_choice.locator("option:checked")).to_have_text("自定义（440像素）")
                page.keyboard.press("Escape")
                separator.focus()
                for key, width in (("Home", "360"), ("End", "640")):
                    separator.press(key)
                    expect(separator).to_have_attribute("aria-valuenow", width)
                    assert page.locator("body").evaluate("node=>node.scrollWidth<=innerWidth")
                separator.press("Home")
                option(page, "重命名会话").click()
                page.get_by_label("会话标题", exact=True).fill("重新命名的会话")
                page.get_by_role("button", name="保存标题", exact=True).click()
                expect(page.locator("#record-dialog")).to_be_hidden()
                assert conversation["title"] == "重新命名的会话"
                history(page)
                page.locator("#assistant-query").fill("合成%_")
                with page.expect_response(
                    lambda response: (
                        "assistant-ui/bootstrap?" in response.url and "q=" in response.url
                    )
                ) as search_response:
                    page.get_by_role("button", name="搜索", exact=True).click()
                assert search_response.value.status == 200
                page.wait_for_function(
                    "document.querySelector('#assistant-search button').disabled===false"
                )
                assert [payload for name, payload in calls if name == "agent.conversation.list"][
                    -1
                ]["q"] == "合成%_"
                page.get_by_role("button", name="关闭历史会话", exact=True).click()
                field.fill("")
                page.get_by_role("button", name="新会话", exact=True).click()
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
                # 触控模拟：不能依靠hover才能找到回复操作；窄屏与低高度仍可发送。
                touch = browser.new_context(
                    viewport={"width": 390, "height": 700}, has_touch=True, is_mobile=True
                )
                touch.add_cookies(
                    [{"name": "office_web_session", "value": web.cookie, "url": base}]
                )
                touch_page = touch.new_page()
                touch_page.goto(base + "/console/assistant/workspace")
                select_conversation(touch_page, conversation["id"])
                expect(
                    touch_page.get_by_role("button", name="复制回复", exact=True).first
                ).to_be_visible()
                assert (
                    touch_page.locator(".assistant-message-actions").first.evaluate(
                        "node=>getComputedStyle(node).opacity"
                    )
                    == "1"
                )
                assert touch_page.locator("body").evaluate("node=>node.scrollWidth<=innerWidth")
                history(touch_page)
                expect(touch_page.locator("#assistant-query")).to_be_visible()
                touch_page.get_by_role("button", name="关闭历史会话", exact=True).click()
                expect(touch_page.locator("[data-assistant-history]")).to_be_focused()
                # 无hover设备接入键盘，聚焦仍显示提示；Esc只关闭提示。
                touch_page.get_by_role("button", name="更多会话操作", exact=True).focus()
                expect(touch_page.get_by_role("tooltip")).to_have_text("更多会话操作")
                touch_page.keyboard.press("Escape")
                expect(touch_page.get_by_role("tooltip")).to_be_hidden()
                # 重排相关模拟，不把窄视口称为真实浏览器缩放验收。
                for viewport in ({"width": 320, "height": 640}, {"width": 640, "height": 256}):
                    touch_page.set_viewport_size(viewport)
                    assert touch_page.locator("body").evaluate("node=>node.scrollWidth<=innerWidth")
                    assert (
                        touch_page.locator("#assistant-input").evaluate(
                            "n=>getComputedStyle(n).fontSize"
                        )
                        == "16px"
                    )
                    assert (
                        touch_page.locator(".assistant-answer").first.evaluate(
                            "n=>getComputedStyle(n).fontSize"
                        )
                        == "16px"
                    )
                    touch_page.locator("#assistant-input").scroll_into_view_if_needed()
                    expect(touch_page.locator("#assistant-input")).to_be_in_viewport()
                    history(touch_page)
                    expect(touch_page.locator("#assistant-query")).to_be_in_viewport()
                    touch_page.get_by_role("button", name="关闭历史会话", exact=True).click()
                touch.close()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
