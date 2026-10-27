"""真实模板与共享JS的隔离状态回归；不访问用户会话、数据库或模型。"""

import asyncio
import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from app.entrypoints.web.responses import TEMPLATES
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from playwright.sync_api import expect, sync_playwright
from ui_catalog import context

pytestmark = pytest.mark.live


@pytest.fixture
def ui_state():
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需显式启用隔离浏览器")
    app = FastAPI()
    state = {"writes": 0, "reads": 0, "delay": False, "release": False, "selected": True}

    @app.get("/console/employees")
    async def listing(request: Request):
        value = context("employees.html")
        value["pending"] = {
            "items": [
                {
                    "id": "pending",
                    "display_name": "合成待认领人员",
                    "subject": "synthetic",
                    "first_seen_at": None,
                    "last_seen_at": None,
                }
            ]
        }
        if request.query_params.get("saved"):
            value["notice"] = "保存完成"
        return TEMPLATES.TemplateResponse(request, "employees.html", value)

    @app.post("/console/employees/{identifier}/update")
    async def update(identifier: str):
        state["writes"] += 1
        while state["delay"] and not state["release"]:
            await asyncio.sleep(0.01)
        return RedirectResponse("/console/employees?saved=1", 303)

    @app.get("/console/links/employees/person/roles")
    async def grants(request: Request):
        value = context("links.html")
        value["assoc"]["ids"] = ["role"] if state["selected"] else []
        if request.query_params.get("saved"):
            value["notice"] = "保存完成"
        return TEMPLATES.TemplateResponse(request, "links.html", value)

    @app.post("/console/links/employees/person/roles")
    async def save_grants(request: Request):
        form = await request.form()
        state["selected"] = "id_role" in form
        state["writes"] += 1
        return RedirectResponse("/console/links/employees/person/roles?saved=1", 303)

    @app.get("/console/roles")
    async def roles(request: Request):
        return TEMPLATES.TemplateResponse(request, "roles.html", context("roles.html"))

    @app.get("/console/assistant/workspace")
    async def workspace(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "assistant_workspace.html", context("assistant_workspace.html")
        )

    @app.get("/console/client-authorizations")
    async def authorizations(request: Request):
        value = context("client_authorizations.html")
        value["listing"]["items"][0]["current"] = True
        return TEMPLATES.TemplateResponse(request, "client_authorizations.html", value)

    @app.post("/console/client-authorizations/session/change")
    async def end_session(request: Request):
        state["writes"] += 1
        return TEMPLATES.TemplateResponse(
            request, "signed_out.html", {"authorization_ended": True, "cleanup": "done"}
        )

    @app.get("/console/signed-out")
    async def signed_out(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "signed_out.html", {"authorization_ended": True, "cleanup": "done"}
        )

    @app.get("/console/assistant-ui/bootstrap")
    async def bootstrap():
        return {
            "code": 0,
            "data": {"capability": {"ready": True}, "conversations": {"items": [], "next": None}},
        }

    @app.post("/console/assistant-ui/action")
    async def agent_action(request: Request):
        state["actions"] = state.get("actions", 0) + 1
        while state.get("agent_pending") and not state.get("agent_release"):
            await asyncio.sleep(0.01)
        return {"code": 0, "data": {"id": "conversation"}}

    @app.get("/console/assistant-ui/conversations/{identifier}")
    async def conversation(identifier: str):
        state["reads"] += 1
        return {
            "code": 0,
            "data": {
                "id": identifier,
                "title": "合成运行任务",
                "version": 1,
                "access_fingerprint": "synthetic",
                "archived": False,
                "next": None,
                "turns": [
                    {
                        "id": "turn",
                        "prompt": "合成工作",
                        "status": "completed" if state.get("completed") else "running",
                        "result": {
                            "receipts": [
                                {
                                    "status": "received",
                                    "operation": "data",
                                    "result": {"changed": True},
                                }
                            ]
                        }
                        if state.get("completed")
                        else None,
                        "created_at": "2026-10-27T00:00:00Z",
                        "version": 1,
                    }
                ],
            },
        }

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.02)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
            )
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(f"http://127.0.0.1:{listener.getsockname()[1]}/console/employees")
                yield page, state
            finally:
                browser.close()
    finally:
        state["release"] = True
        state["agent_release"] = True
        server.should_exit = True
        thread.join(5)
        listener.close()


def edit_name(page):
    page.locator("summary").filter(has_text="修改姓名").click()
    page.locator("#record-dialog-content input[name=name]").fill("合成修改")


def test_save_preserves_shell_and_assistant_binding(ui_state):
    page, _ = ui_state
    page.evaluate(
        "window.originalLaunch=document.querySelector('#assistant-launch');window.originalContext=document.querySelector('.shell-context')"
    )
    edit_name(page)
    page.locator("#record-dialog-content button[type=submit]").click()
    expect(page.locator("#record-dialog")).not_to_have_attribute("open", "")
    expect(page.locator("main")).not_to_have_attribute("aria-busy", "true")
    expect(page.locator("#ui-notifications")).to_contain_text("保存完成")
    assert page.evaluate("originalLaunch.isConnected && originalContext.isConnected")
    for _ in range(2):
        page.locator("#assistant-launch").click()
        expect(page.locator("#assistant-panel")).to_be_visible()
        page.get_by_role("button", name="收起智能助手", exact=True).click()
        expect(page.locator("#assistant-launch")).to_be_focused()


def test_other_form_draft_checked_before_write(ui_state):
    page, state = ui_state
    page.locator(".pending-queue > summary").click()
    page.locator(".claim-name").fill("未保存的姓名")
    page.locator("form[data-confirm-message] button[type=submit]").click()
    expect(page.locator("#confirm-title")).to_have_text("放弃未保存的修改？")
    page.locator("[data-confirm-cancel]").click()
    assert state["writes"] == 0
    expect(page.locator(".claim-name")).to_have_value("未保存的姓名")
    page.locator("form[data-confirm-message] button[type=submit]").click()
    page.locator("[data-confirm-accept]").click()
    expect(page.locator("#confirm-description")).to_contain_text("确认停用")
    page.locator("[data-confirm-accept]").click()
    expect(page.locator(".claim-name")).to_have_value("")
    assert state["writes"] == 1


def test_new_draft_during_write_defers_refresh(ui_state):
    page, state = ui_state
    state["delay"] = True
    page.locator(".pending-queue > summary").click()
    page.locator("form[data-confirm-message] button[type=submit]").click()
    page.locator("[data-confirm-accept]").click()
    expect(page.locator("form[data-confirm-message]")).to_have_attribute("aria-busy", "true")
    page.locator(".claim-name").fill("请求等待期间的新草稿")
    state["release"] = True
    expect(page.locator("#ui-notifications")).to_contain_text("列表未自动刷新")
    expect(page.locator(".claim-name")).to_have_value("请求等待期间的新草稿")
    assert state["writes"] == 1


def test_full_page_grant_save_is_not_misclassified_as_unsaved(ui_state):
    page, state = ui_state
    page.goto(page.url.replace("/console/employees", "/console/links/employees/person/roles"))
    page.locator('[name="id_role"]').uncheck()
    page.locator("[data-grant-editor] button[type=submit]").click()
    expect(page.locator("#ui-notifications")).to_contain_text("保存完成")
    expect(page.locator("#ui-notifications")).not_to_contain_text("列表未自动刷新")
    expect(page.locator("#grant-status")).to_have_text("当前没有未保存的修改。")
    expect(page.locator('[name="id_role"]')).not_to_be_checked()
    assert state["writes"] == 1


@pytest.mark.parametrize("result", ["success", "conflict", "network"])
def test_disabled_state_restored_after_write(ui_state, result):
    page, state = ui_state
    edit_name(page)
    page.locator("#record-dialog-content form").evaluate(
        "form=>{window.savedForm=form;const b=document.createElement('button');b.type='submit';b.disabled=true;b.id='original-disabled';b.textContent='合成禁用控件';form.append(b);window.originalDisabled=b}"
    )
    if result == "conflict":
        page.route(
            "**/console/employees/person/update",
            lambda route: route.fulfill(
                status=409,
                content_type="text/html",
                body='<main><section class="card">合成冲突</section></main>',
            ),
        )
    elif result == "network":
        page.route("**/console/employees/person/update", lambda route: route.abort())
    # 成功时节点移回原details，失败时仍在详情弹窗中，均需保持原状态。
    page.locator("#record-dialog-content button[type=submit]:not(#original-disabled)").click()
    if result == "success":
        expect(page.locator("#record-dialog")).not_to_have_attribute("open", "")
    else:
        expect(page.locator("[data-operation-error]")).to_be_visible()
    # 列表成功刷新会替换原表单，保存被移除节点引用以核对收尾。
    page.wait_for_function("!savedForm.hasAttribute('aria-busy')")
    assert page.evaluate("originalDisabled.disabled")
    assert not page.evaluate("savedForm.inert")
    if result != "success":
        expect(page.locator("#original-disabled")).to_be_disabled()
        expect(
            page.locator("#record-dialog-content button[type=submit]:not(#original-disabled)")
        ).to_be_enabled()
    assert state["writes"] == (1 if result == "success" else 0)


@pytest.mark.parametrize("action", ["navigation", "reload"])
def test_cancel_navigation_keeps_assistant_polling(ui_state, action):
    page, state = ui_state
    page.evaluate(
        "sessionStorage.setItem('office-assistant:catalog-admin',JSON.stringify({id:'conversation',open:true}))"
    )
    page.reload()
    expect(page.locator('[data-run="turn"]')).to_be_visible()
    page.locator("#assistant-input").fill("保留草稿")
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    if action == "navigation":
        page.locator('.shell-nav a[href="/console/roles"]').click()
        expect(page.locator("#confirm-title")).to_have_text("离开并放弃助手草稿？")
        page.locator("[data-confirm-cancel]").click()
        assert dialogs == []
    else:
        # 取消刷新本就不会完成导航，不等待一个注定不会到来的load事件。
        page.evaluate("location.reload()")
        assert dialogs == ["beforeunload"]
    assert page.url.endswith("/console/employees")
    previous = state["reads"]
    page.wait_for_function("document.querySelector('#assistant-messages [data-run=turn]') !== null")
    # 等待实际下一次HTTP读取，不以静态源码字符串作为生命周期断言。
    deadline = time.monotonic() + 5
    while state["reads"] <= previous and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert state["reads"] > previous
    expect(page.locator("#assistant-input")).to_have_value("保留草稿")


@pytest.mark.parametrize("full", [False, True])
def test_confirm_draft_navigation_has_no_second_browser_prompt(ui_state, full):
    page, state = ui_state
    if full:
        page.goto(page.url.replace("/console/employees", "/console/assistant/workspace"))
    else:
        page.locator("#assistant-launch").click()
    page.locator("#assistant-input").fill("不自动持久化的助手草稿")
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    page.locator('.shell-nav a[href="/console/roles"]').click()
    expect(page.locator("#action-confirm")).to_be_visible()
    proof = Path("runtime/agent-research/ui-controls")
    proof.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(proof / f"navigation-{'workspace' if full else 'sidebar'}.png"))
    expect(page.locator("[data-confirm-cancel]")).to_have_text("继续编辑")
    page.locator("[data-confirm-cancel]").click()
    expect(page.locator("#assistant-input")).to_have_value("不自动持久化的助手草稿")
    page.locator('.shell-nav a[href="/console/roles"]').click()
    page.locator("[data-confirm-accept]").click()
    expect(page).to_have_url(re.compile(r"/console/roles$"))
    assert dialogs == []
    assert state["writes"] == 0
    assert "不自动持久化的助手草稿" not in page.evaluate("JSON.stringify(sessionStorage)")


def test_filter_navigation_checks_assistant_draft(ui_state):
    page, _ = ui_state
    page.locator("#assistant-launch").click()
    page.locator("#assistant-input").fill("筛选前草稿")
    page.locator("main input[name=q]").fill("人员查询")
    page.locator("main form[role=search] button[type=submit]").click()
    expect(page.locator("#confirm-title")).to_have_text("离开并放弃助手草稿？")
    page.locator("[data-confirm-cancel]").click()
    expect(page.locator("#assistant-input")).to_have_value("筛选前草稿")
    assert "q=" not in page.url


def test_cancel_page_size_restores_current_selection(ui_state):
    page, _ = ui_state
    page.locator("#assistant-launch").click()
    page.locator("#assistant-input").fill("分页前草稿")
    page.get_by_label("每页条数", exact=True).select_option("20")
    expect(page.locator("#confirm-title")).to_have_text("离开并放弃助手草稿？")
    page.locator("[data-confirm-cancel]").click()
    expect(page.get_by_label("每页条数", exact=True)).to_have_value("10")
    expect(page.locator("#assistant-input")).to_have_value("分页前草稿")


def test_native_picker_visuals_keyboard_and_reduced_motion(ui_state):
    page, _ = ui_state
    field = page.get_by_label("每页条数", exact=True)
    field.focus()
    assert field.evaluate("node=>getComputedStyle(node).outlineWidth") == "2px"
    assert field.evaluate("node=>getComputedStyle(node).borderRadius") == "8px"
    enhanced = field.evaluate("node=>getComputedStyle(node).appearance==='base-select'")
    assert enhanced == page.evaluate(
        "CSS.supports('appearance','base-select') && CSS.supports('selector(select::picker(select))') && matchMedia('(hover:hover) and (pointer:fine)').matches"
    )
    if enhanced:
        field.click()
        assert field.evaluate("node=>node.matches(':open')")
        assert (
            field.evaluate("node=>getComputedStyle(node,'::picker(select)').borderRadius") == "10px"
        )
        proof = Path("runtime/agent-research/ui-controls")
        proof.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(proof / "native-select.png"))
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
    else:
        field.select_option("20")
    expect(page).to_have_url(re.compile(r"limit=20"))
    page.emulate_media(reduced_motion="reduce")
    assert (
        page.get_by_label("每页条数", exact=True).evaluate(
            "node=>getComputedStyle(node).transitionDuration"
        )
        == "0s"
    )


def test_picker_escape_keeps_parent_assistant_menu(ui_state):
    page, _ = ui_state
    page.locator("#assistant-launch").click()
    page.get_by_role("button", name="更多会话操作", exact=True).click()
    field = page.get_by_label("侧栏宽度", exact=True)
    if field.evaluate("node=>getComputedStyle(node).appearance!=='base-select'"):
        pytest.skip("当前浏览器使用系统原生弹层，另有普通流程回归")
    field.click()
    assert field.evaluate("node=>node.matches(':open')")
    page.keyboard.press("Escape")
    expect(page.locator("#assistant-options-panel")).to_be_visible()
    expect(page.locator("#assistant-panel")).to_be_visible()
    assert not field.evaluate("node=>node.matches(':open')")


def test_touch_and_no_script_select_fallbacks(ui_state):
    page, _ = ui_state
    browser = page.context.browser
    for settings in ({"has_touch": True, "is_mobile": True}, {"java_script_enabled": False}):
        isolated = browser.new_context(viewport={"width": 390, "height": 900}, **settings)
        try:
            fallback = isolated.new_page()
            fallback.goto(page.url)
            field = fallback.get_by_label("每页条数", exact=True)
            if settings.get("has_touch"):
                assert field.evaluate("node=>getComputedStyle(node).appearance") != "base-select"
            field.select_option("20")
            if settings.get("java_script_enabled") is False:
                fallback.locator("[data-page-size-form] button[type=submit]").click()
            expect(fallback).to_have_url(re.compile("limit=20"))
        finally:
            isolated.close()


def test_picker_escape_inside_shared_dialog(ui_state):
    page, _ = ui_state
    page.evaluate("""() => {
      const box=document.createElement('div'),label=document.createElement('label');
      label.textContent='合成选项';const select=document.createElement('select');
      select.id='dialog-select';label.htmlFor=select.id;
      for(const name of ['第一项','第二项']){const option=document.createElement('option');option.textContent=name;select.append(option);}
      box.append(label,select);OfficeUI.present('选择器回归',box);
    }""")
    field = page.locator("#dialog-select")
    if field.evaluate("node=>getComputedStyle(node).appearance!=='base-select'"):
        pytest.skip("浏览器采用系统选择器")
    field.click()
    page.keyboard.press("Escape")
    expect(page.locator("#record-dialog")).to_be_visible()
    assert not field.evaluate("node=>node.matches(':open')")
    page.locator("[data-record-close]").click()
    expect(page.locator("#record-dialog")).to_be_hidden()


def test_same_document_anchor_does_not_discard_draft(ui_state):
    page, _ = ui_state
    page.locator("#assistant-launch").click()
    page.locator("#assistant-input").fill("锚点跳转保留")
    page.locator(".skip-link").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#action-confirm")).not_to_be_visible()
    expect(page.locator("#assistant-input")).to_have_value("锚点跳转保留")


@pytest.mark.parametrize("assistant_draft", [False, True])
def test_assistant_business_refresh_has_one_confirmation_per_draft(ui_state, assistant_draft):
    page, state = ui_state
    state["completed"] = True
    page.evaluate(
        "sessionStorage.setItem('office-assistant:catalog-admin',JSON.stringify({id:'conversation',open:true}))"
    )
    page.reload()
    expect(page.get_by_role("button", name="刷新业务页面", exact=True)).to_be_visible()
    page.locator(".pending-queue > summary").click()
    page.locator(".claim-name").fill("未保存的人员资料")
    if assistant_draft:
        page.locator("#assistant-input").fill("未发送的助手草稿")
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    page.evaluate("window.refreshProbe=true")
    page.get_by_role("button", name="刷新业务页面", exact=True).click()
    expect(page.locator("#confirm-title")).to_have_text("刷新业务页面？")
    page.locator("[data-confirm-accept]").click()
    if assistant_draft:
        expect(page.locator("#confirm-title")).to_have_text("离开并放弃助手草稿？")
        page.locator("[data-confirm-accept]").click()
    page.wait_for_function("window.refreshProbe===undefined", timeout=3000)
    assert dialogs == []
    expect(page.locator(".claim-name")).to_have_value("")


@pytest.mark.parametrize("target", ["navigation", "filter"])
def test_pending_write_blocks_page_exit_until_receipt(ui_state, target):
    page, state = ui_state
    state["delay"] = True
    page.locator("form[data-confirm-message] button[type=submit]").click()
    page.locator("[data-confirm-accept]").click()
    expect(page.locator("form[data-confirm-message]")).to_have_attribute("aria-busy", "true")
    if target == "navigation":
        page.locator('.shell-nav a[href="/console/roles"]').click()
    else:
        page.locator("main input[name=q]").fill("等候保存")
        page.locator("main form[role=search] button[type=submit]").click()
    expect(page.locator("#ui-notifications")).to_contain_text("操作正在提交")
    assert page.url.endswith("/console/employees")
    state["release"] = True
    expect(page.locator("#ui-notifications")).to_contain_text("保存完成")
    page.locator('.shell-nav a[href="/console/roles"]').click()
    expect(page).to_have_url(re.compile(r"/console/roles$"))
    assert state["writes"] == 1


def test_browser_refresh_during_write_can_be_cancelled_without_losing_receipt(ui_state):
    page, state = ui_state
    state["delay"] = True
    page.locator("form[data-confirm-message] button[type=submit]").click()
    page.locator("[data-confirm-accept]").click()
    expect(page.locator("form[data-confirm-message]")).to_have_attribute("aria-busy", "true")
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    page.evaluate("location.reload()")
    assert dialogs == ["beforeunload"]
    state["release"] = True
    expect(page.locator("#ui-notifications")).to_contain_text("保存完成")
    assert state["writes"] == 1


def test_pending_agent_submission_blocks_exit_but_running_task_does_not(ui_state):
    page, state = ui_state
    state["agent_pending"] = True
    page.locator("#assistant-launch").click()
    page.locator("#assistant-input").fill("合成受理验证")
    expect(page.locator("[data-assistant-send]")).to_be_enabled()
    page.locator("[data-assistant-send]").click()
    page.locator('.shell-nav a[href="/console/roles"]').click()
    expect(page.locator("#ui-notifications")).to_contain_text("助手操作正在提交")
    assert page.url.endswith("/console/employees")
    state["agent_release"] = True
    expect(page.locator("#assistant-input")).to_have_value("")
    expect(page.locator('[data-run="turn"]')).to_be_visible()
    page.locator('.shell-nav a[href="/console/roles"]').click()
    expect(page).to_have_url(re.compile(r"/console/roles$"))
    assert state["actions"] == 2


def test_ending_current_authorization_confirms_draft_before_write(ui_state):
    page, state = ui_state
    page.goto(page.url.replace("/console/employees", "/console/client-authorizations"))
    page.locator("#assistant-launch").click()
    page.locator("#assistant-input").fill("退出前的草稿")
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    page.locator("summary").filter(has_text="停用授权").click()
    page.locator("#record-dialog-content input[name=reason]").fill("合成退出验证")
    page.locator("#record-dialog-content button[type=submit]").click()
    page.locator("[data-confirm-accept]").click()
    expect(page.locator("#confirm-title")).to_have_text("离开并放弃助手草稿？")
    page.locator("[data-confirm-cancel]").click()
    assert state["writes"] == 0
    page.locator("#record-dialog-content button[type=submit]").click()
    page.locator("[data-confirm-accept]").click()
    expect(page.locator("#confirm-title")).to_have_text("离开并放弃助手草稿？")
    page.locator("[data-confirm-accept]").click()
    expect(page).to_have_url(re.compile(r"/console/signed-out$"))
    assert state["writes"] == 1
    assert dialogs == []
