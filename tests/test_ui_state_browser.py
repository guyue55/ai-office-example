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
from fastapi.responses import HTMLResponse, RedirectResponse
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
        value["pending"] = {"items": [], "pagination": {"total": 1}}
        if request.query_params.get("saved"):
            value["notice"] = "保存完成"
        response = TEMPLATES.TemplateResponse(request, "employees.html", value)
        # 通用草稿协议使用独立合成表单，避免依赖已迁出的业务队列。
        editor = '<form method="post" action="/fixture/draft"><label>独立草稿<input class="draft-name" name="draft" value=""></label></form>'
        return HTMLResponse(response.body.decode().replace("</main>", editor + "</main>"))

    @app.post("/console/employees/{identifier}/update")
    async def update(identifier: str):
        state["writes"] += 1
        while state["delay"] and not state["release"]:
            await asyncio.sleep(0.01)
        return RedirectResponse("/console/employees?saved=1", 303)

    @app.get("/console/links/employees/person/roles")
    async def grants(request: Request):
        state["panel_reads"] = state.get("panel_reads", 0) + 1
        while state.get("panel_delay") and not state.get("panel_release"):
            await asyncio.sleep(0.01)
        value = context("links.html")
        value["assoc"]["ids"] = ["role"] if state["selected"] else []
        if state.get("bulk_samples"):
            value["candidates"] = [
                {"id": "role", "name": "已选角色", "permission_count": 2, "employee_count": 1},
                {"id": "second", "name": "候选角色", "permission_count": 1, "employee_count": 0},
            ]
            value["assoc"]["ids"] = ["role", "kept"]
            value["preserved_items"] = [{"id": "kept", "name": "跨页保留角色"}]

        if request.query_params.get("saved"):
            value["notice"] = "保存完成"
        return TEMPLATES.TemplateResponse(
            request, "links.html", value, status_code=503 if state.get("panel_fail") else 200
        )

    @app.post("/console/links/employees/person/roles")
    async def save_grants(request: Request):
        form = await request.form()
        state["selected"] = "id_role" in form
        state["writes"] += 1
        return RedirectResponse("/console/links/employees/person/roles?saved=1", 303)

    @app.get("/console/accounts/link")
    async def link_account(request: Request):
        value = context("account_link.html")
        value["search"] = request.query_params.get("q", "")
        value["found"] = {
            "notice": "合成账号结果",
            "items": [
                {
                    "id": "remote-one",
                    "name": "候选人员",
                    "username": "candidate",
                    "email": "",
                    "enabled": True,
                    "linked_employee_id": None,
                },
            ],
        }
        return TEMPLATES.TemplateResponse(request, "account_link.html", value)

    @app.get("/console/accounts/new")
    async def new_account(request: Request):
        if state.get("server_error"):
            value = context("error.html")
            value.update(
                message="账号管理连接暂不可用，请联系管理员检查服务配置",
                code="identity_unavailable",
            )
            return TEMPLATES.TemplateResponse(request, "error.html", value, status_code=503)
        return TEMPLATES.TemplateResponse(
            request,
            "account_new.html",
            context("account_new.html"),
            status_code=state.get("panel_status", 503) if state.get("panel_fail") else 200,
        )

    @app.get("/console/dashboard")
    async def dashboard(request: Request):
        return TEMPLATES.TemplateResponse(request, "dashboard.html", context("dashboard.html"))

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
        state["panel_release"] = True
        server.should_exit = True
        thread.join(5)
        listener.close()


def edit_name(page):
    page.locator("summary").filter(has_text="修改姓名").click()
    page.locator("#record-dialog-content input[name=name]").fill("合成修改")


def test_mobile_escape_closes_only_topmost_account_layer(ui_state):
    page, state = ui_state
    page.set_viewport_size({"width": 390, "height": 844})
    navigation = page.locator("[data-navigation-toggle]")
    menu = page.locator("[data-account-menu]")
    navigation.click()
    menu.locator("summary").click()
    page.keyboard.press("Escape")
    expect(menu).not_to_have_attribute("open", "")
    expect(navigation).to_have_attribute("aria-expanded", "true")
    expect(menu.locator("summary")).to_be_focused()
    page.keyboard.press("Escape")
    expect(navigation).to_have_attribute("aria-expanded", "false")
    expect(navigation).to_be_focused()
    navigation.click()
    menu.locator("summary").click()
    page.evaluate("() => { void OfficeUI.confirm('仅验证焦点',{title:'合成确认'}); }")
    expect(page.locator("#action-confirm")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#action-confirm")).to_be_hidden()
    expect(navigation).to_have_attribute("aria-expanded", "true")
    assert state["writes"] == 0


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
    page.locator(".draft-name").fill("未保存的姓名")
    page.locator("form[data-confirm-message] button[type=submit]").click()
    expect(page.locator("#confirm-title")).to_have_text("放弃未保存的修改？")
    page.locator("[data-confirm-cancel]").click()
    assert state["writes"] == 0
    expect(page.locator(".draft-name")).to_have_value("未保存的姓名")
    page.locator("form[data-confirm-message] button[type=submit]").click()
    page.locator("[data-confirm-accept]").click()
    expect(page.locator("#confirm-description")).to_contain_text("确认停用")
    page.locator("[data-confirm-accept]").click()
    expect(page.locator(".draft-name")).to_have_value("")
    assert state["writes"] == 1


def test_new_draft_during_write_defers_refresh(ui_state):
    page, state = ui_state
    state["delay"] = True
    page.locator("form[data-confirm-message] button[type=submit]").click()
    page.locator("[data-confirm-accept]").click()
    expect(page.locator("form[data-confirm-message]")).to_have_attribute("aria-busy", "true")
    page.locator(".draft-name").fill("请求等待期间的新草稿")
    state["release"] = True
    expect(page.locator("#ui-notifications")).to_contain_text("列表未自动刷新")
    expect(page.locator(".draft-name")).to_have_value("请求等待期间的新草稿")
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
    assert field.evaluate("node=>getComputedStyle(node).outlineOffset") == "0px"
    assert field.evaluate("node=>getComputedStyle(node).boxShadow") == "none"
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
    page.locator(".draft-name").fill("未保存的人员资料")
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
    expect(page.locator(".draft-name")).to_have_value("")


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


def test_shared_disabled_readonly_busy_and_touch_control_states(ui_state):
    page, _ = ui_state
    field = page.get_by_role("searchbox", name="搜索", exact=True)
    field.evaluate("node=>node.readOnly=true")
    field.click()
    expect(field).to_have_css("background-color", "rgb(245, 247, 249)")
    assert field.evaluate("node=>document.activeElement===node")
    field.evaluate("node=>{node.readOnly=false;node.disabled=true}")
    expect(field).to_be_disabled()
    field.hover(force=True)
    assert field.evaluate("node=>getComputedStyle(node).cursor") == "not-allowed"
    button = page.get_by_role("button", name="搜索", exact=True)
    button.evaluate("node=>node.disabled=true")
    background = button.evaluate("node=>getComputedStyle(node).backgroundColor")
    button.hover(force=True)
    assert button.evaluate("node=>getComputedStyle(node).backgroundColor") == background
    assert button.evaluate("node=>getComputedStyle(node).cursor") == "not-allowed"
    button.evaluate("node=>node.form.setAttribute('aria-busy','true')")
    assert button.evaluate("node=>getComputedStyle(node).cursor") == "wait"
    page.emulate_media(reduced_motion="reduce")
    assert button.evaluate("node=>getComputedStyle(node).transitionDuration") == "0s"
    # 触屏上下文检验实际共享图标尺寸，不能以桌面截图推断。
    touch = page.context.browser.new_context(
        has_touch=True, is_mobile=True, viewport={"width": 390, "height": 844}
    )
    try:
        touch_page = touch.new_page()
        touch_page.goto(page.url)
        assert touch_page.evaluate("matchMedia('(pointer:coarse)').matches")
        icon = touch_page.locator(".action-cell .icon-action").first
        expect(icon).to_be_visible()
        box = icon.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44
    finally:
        touch.close()


def test_drawer_loading_cancel_reopen_and_failure_retry(ui_state):
    page, state = ui_state
    state["panel_delay"] = True
    trigger = page.get_by_role("link", name="授权角色", exact=True)
    trigger.click()
    panel = page.locator("#workspace")
    expect(panel).to_be_visible()
    expect(page.locator("#workspace-content")).to_have_attribute("aria-busy", "true")
    expect(page.locator("#workspace-feedback[data-loading]")).to_contain_text("正在加载")
    expect(panel).to_have_css("opacity", "1")
    proof = Path("runtime/agent-research/ui-controls")
    proof.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(proof / "drawer-loading-review.png"))
    page.get_by_role("button", name="关闭操作面板", exact=True).click()
    expect(panel).to_be_hidden()
    state["panel_delay"] = False
    state["panel_fail"] = True
    trigger.click()
    expect(page.locator("#workspace-feedback")).to_have_class("workspace-feedback error-feedback")
    expect(page.locator("#workspace-content")).not_to_have_attribute("aria-busy", "true")
    state["panel_fail"] = False
    page.get_by_role("button", name="刷新配置", exact=True).click()
    expect(page.locator("#workspace-content [data-grant-editor]")).to_be_visible()
    expect(page.locator("#workspace-feedback")).to_be_empty()
    assert state["panel_reads"] >= 3
    state["panel_release"] = True
    expect(page.locator("#workspace-content [data-grant-editor]")).to_be_visible()
    page.emulate_media(reduced_motion="reduce")
    assert panel.evaluate("node=>getComputedStyle(node).animationName") == "none"
    page.get_by_role("button", name="关闭操作面板", exact=True).click()
    expect(trigger).to_be_focused()
    assert state["writes"] == 0


@pytest.mark.parametrize("status", [403, 503])
def test_compact_dialog_failure_has_visible_retry(ui_state, status):
    page, state = ui_state
    state["panel_fail"] = True
    state["panel_status"] = status
    page.get_by_role("link", name="新建员工", exact=True).click()
    expect(page.locator("#workspace.is-compact")).to_be_visible()
    expect(page.locator("#workspace-feedback")).to_have_class("workspace-feedback error-feedback")
    expect(page.locator("#workspace-title")).to_have_text("加载未完成")
    expect(page.locator("#workspace-feedback")).to_contain_text(
        "无权访问" if status == 403 else "HTTP 503"
    )
    expect(page.locator("#workspace-feedback")).not_to_contain_text("登录账号")
    retry = page.get_by_role("button", name="刷新配置", exact=True)
    expect(retry).to_be_visible()
    state["panel_fail"] = False
    retry.click()
    expect(page.locator('#workspace-content input[name="username"]')).to_be_visible()
    expect(page.locator("#workspace-feedback")).to_be_empty()
    expect(retry).to_be_hidden()
    page.get_by_role("button", name="关闭操作面板", exact=True).click()
    assert state["writes"] == 0


def test_compact_dialog_shows_bounded_server_reason_and_recovers(ui_state):
    page, state = ui_state
    state["server_error"] = True
    page.get_by_role("link", name="新建员工", exact=True).click()
    feedback = page.locator("#workspace-feedback")
    expect(feedback).to_be_visible()
    expect(feedback).to_contain_text("账号管理连接暂不可用")
    expect(feedback).not_to_contain_text("工作台")
    state["server_error"] = False
    page.get_by_role("button", name="刷新配置", exact=True).click()
    expect(page.locator('#workspace-content input[name="username"]')).to_be_visible()
    expect(feedback).to_be_empty()
    assert state["writes"] == 0


def test_dashboard_cards_open_details_without_stacking_drawer(ui_state):
    page, state = ui_state
    page.goto(page.url.replace("/console/employees", "/console/dashboard"))
    permission = page.locator('[data-overview-open="my-permissions"]')
    roles = page.locator('[data-overview-open="my-roles"]')
    expect(page.locator("#my-permissions")).to_be_hidden()
    expect(page.locator("#my-roles")).to_be_hidden()
    permission.click()
    dialog = page.locator("#record-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("table")).to_be_visible()
    expect(page.locator("dialog[open]")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(permission).to_be_focused()
    roles.click()
    expect(dialog).to_be_visible()
    link = dialog.get_by_role("link", name="查看配置", exact=True)
    expect(link).to_be_visible()
    expect(link).to_have_attribute("data-workspace-full", "")
    page.wait_for_timeout(200)  # 观察增强重绘后，完整页面入口仍需可见。
    expect(link).to_be_visible()
    page.get_by_role("button", name="关闭详情", exact=True).click()
    expect(roles).to_be_focused()
    roles.click()
    expect(dialog.get_by_role("table")).to_be_visible()
    expect(page.locator("#workspace")).not_to_be_visible()
    page.route(
        "**/console/access/roles/*",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<main>角色配置完整页面</main>"
        ),
    )
    dialog.get_by_role("link", name="查看配置", exact=True).click()
    expect(page).to_have_url(re.compile(r"/console/access/roles/"))
    expect(page.locator("dialog[open]")).to_have_count(0)
    plain = page.context.browser.new_context(java_script_enabled=False)
    try:
        fallback = plain.new_page()
        fallback.goto(page.url.split("/console/")[0] + "/console/dashboard")
        expect(fallback.locator("#my-roles table")).to_be_visible()
        expect(fallback.locator("#my-permissions table")).to_be_visible()
    finally:
        plain.close()
    assert state["writes"] == 0


def test_long_table_dialog_keeps_close_visible_and_restores_focus(ui_state):
    page, state = ui_state
    page.goto(page.url.replace("/console/employees", "/console/dashboard"))
    trigger = page.locator('[data-overview-open="my-permissions"]')
    for width, height in [(1440, 900), (390, 600), (844, 390)]:
        page.set_viewport_size({"width": width, "height": height})
        trigger.click()
        dialog = page.locator("#record-dialog")
        # 合成长表验证滚动容器，而非依赖当前账号恰好拥有大量权限。
        page.locator("#record-dialog-content tbody").evaluate(
            "body => { const row = body.firstElementChild; "
            "while (body.children.length < 80) body.append(row.cloneNode(true)); }"
        )
        size = dialog.bounding_box()
        assert size is not None
        assert size["width"] <= width
        assert size["height"] <= height * 0.85 + 1
        if width == 1440:
            assert size["width"] >= 900
        content = page.locator("#record-dialog-content")
        assert content.evaluate("el => el.scrollHeight > el.clientHeight")
        content.evaluate("el => el.scrollTop = el.scrollHeight")
        close = page.get_by_role("button", name="关闭详情", exact=True)
        expect(close).to_be_in_viewport()
        close.click()
        expect(dialog).to_be_hidden()
        expect(trigger).to_be_focused()
    assert state["writes"] == 0


def test_management_table_header_stays_sticky_for_long_lists(ui_state):
    page, state = ui_state
    page.goto(page.url.replace("/console/employees", "/console/employees"))
    table = page.locator(".management-table").first
    expect(table).to_be_visible()
    header = table.locator("thead th").first
    assert header.evaluate("el => getComputedStyle(el).position") == "sticky"
    assert header.evaluate("el => getComputedStyle(el).zIndex") == "1"
    assert state["writes"] == 0


def test_grant_page_selection_preserves_other_page_and_resets(ui_state):
    page, state = ui_state
    state["bulk_samples"] = True
    page.goto(page.url.replace("/console/employees", "/console/links/employees/person/roles"))
    bulk = page.get_by_role("checkbox", name="选择本页全部选项", exact=True)
    expect(bulk).to_be_visible()
    expect(bulk).to_have_js_property("indeterminate", True)
    outside = page.locator('[name="id_kept"]')
    expect(outside).to_be_checked()
    bulk.check()
    expect(page.locator('[name="id_second"]')).to_be_checked()
    expect(page.locator("[data-selection-count]")).to_contain_text("本页已选 2 / 2 项")
    bulk.uncheck()
    expect(outside).to_be_checked()
    expect(page.locator("[data-selection-count]")).to_contain_text("其他页或筛选外已选 1 项")
    page.get_by_role("button", name="撤销本次修改", exact=True).click()
    expect(page.locator('[name="id_role"]')).to_be_checked()
    expect(page.locator('[name="id_second"]')).not_to_be_checked()
    expect(bulk).to_have_js_property("indeterminate", True)
    expect(page.locator("#grant-status")).to_have_text("当前没有未保存的修改。")
    assert state["writes"] == 0


def test_account_candidate_expands_only_when_selected(ui_state):
    page, state = ui_state
    page.goto(page.url.replace("/console/employees", "/console/accounts/link?q=candidate"))
    editor = page.locator(".account-candidate-edit")
    expect(editor.locator('input[name="name"]')).not_to_be_visible()
    expect(page.get_by_text("候选人员 · candidate", exact=True)).to_be_visible()
    editor.locator("summary").click()
    expect(editor.locator('input[name="name"]')).to_have_value("候选人员")
    expect(editor.locator('input[name="remote_id"]')).to_have_value("remote-one")
    expect(editor.locator('input[name="lookup"]')).to_have_value("candidate")
    expect(page.locator("dialog[open]")).to_have_count(0)
    assert state["writes"] == 0


def test_help_has_explicit_entry_and_focus(ui_state):
    page, state = ui_state
    help_link = page.get_by_role("link", name="授权配置帮助", exact=True)
    help_link.click()
    expect(page.locator("#record-dialog .flow")).to_be_visible()
    expect(page.locator("dialog[open]")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(help_link).to_be_focused()
    expect(page.get_by_role("link", name="外部身份接入", exact=True)).to_have_count(0)
    expect(page.locator('input[name="subject"]')).to_have_count(0)
    assert state["writes"] == 0
