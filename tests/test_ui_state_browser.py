"""真实模板与共享JS的隔离状态回归；不访问用户会话、数据库或模型。"""

import asyncio
import os
import socket
import threading
import time

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

    @app.get("/console/assistant-ui/bootstrap")
    async def bootstrap():
        return {
            "code": 0,
            "data": {"capability": {"ready": True}, "conversations": {"items": [], "next": None}},
        }

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
                        "status": "running",
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


def test_cancel_navigation_keeps_assistant_polling(ui_state):
    page, state = ui_state
    page.evaluate(
        "sessionStorage.setItem('office-assistant:catalog-admin',JSON.stringify({id:'conversation',open:true}))"
    )
    page.reload()
    expect(page.locator('[data-run="turn"]')).to_be_visible()
    page.locator("#assistant-input").fill("保留草稿")
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    page.locator('.shell-nav a[href="/console/roles"]').click()
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
