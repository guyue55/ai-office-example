"""实际模板的全页面巡视；合成上下文，不能替代权限／身份／数据库功能测试。"""

import json
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
from fastapi.responses import FileResponse
from playwright.sync_api import expect, sync_playwright
from ui_catalog import PAGE_PATHS, context

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[1]


def test_all_page_templates_at_desktop_and_narrow_widths():
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅隔离模板上下文")
    inventory = json.loads((ROOT / "tests/ui-pages.json").read_text())
    templates = {row["template"] for row in inventory["pages"]}
    assert set(PAGE_PATHS) == templates
    app = FastAPI()

    @app.get("/catalog/{template}")
    async def catalog(request: Request, template: str):
        assert template in templates
        value = context(template)
        # 使用对应页面路径渲染导航与工作区，仍只有合成展示上下文。
        path = PAGE_PATHS[template]
        view_request = Request({**request.scope, "path": path, "raw_path": path.encode()})
        return TEMPLATES.TemplateResponse(view_request, template, value)

    @app.get("/assets/assistant/markdown-it-15.0.3.js")
    async def markdown_asset():
        return FileResponse(
            ROOT
            / "office-data-service/app/entrypoints/web/static/markdown-it-15.0.3/markdown-it.min.js"
        )

    @app.get("/console/assistant-ui/bootstrap")
    async def bootstrap():
        return {
            "code": 0,
            "data": {
                "capability": {"ready": False, "message": "隔离巡视未配置模型。"},
                "conversations": {"items": [], "next": None},
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
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        proof = ROOT / "runtime/agent-research/ui-catalog"
        proof.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
            )
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors, visited = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # 普通页面与完整助手共享位置栏基线，避免切换时跳动。
                for width in (1440, 1024, 768, 390, 320):
                    page.set_viewport_size({"width": width, "height": 1000})
                    positions = []
                    for view in ("employees.html", "assistant_workspace.html"):
                        page.goto(base + "/catalog/" + view)
                        positions.append(page.locator(".shell-context").bounding_box())
                    assert abs(positions[0]["y"] - positions[1]["y"]) < 1, (width, positions)
                    assert abs(positions[0]["x"] - positions[1]["x"]) < 1, (width, positions)
                for template in sorted(templates):
                    response = page.goto(base + "/catalog/" + template)
                    assert response.status == 200, template
                    expect(page.locator("main h1")).to_have_count(1)
                    expect(page.locator("#assistant-panel")).to_have_count(1)
                    expect(page.locator(".shell-nav")).to_have_count(1)
                    if template in {"links.html", "access_detail.html"}:
                        expect(page.locator(".breadcrumb")).to_have_count(0)
                        expect(page.locator(".shell-context")).to_be_visible()
                    if template == "assistant_workspace.html":
                        expect(page.locator(".assistant-welcome h2")).to_be_visible()
                    if template == "permissions.html":
                        # 连续长编码也不能撑开窄屏权限卡片。
                        page.locator(".data-table .code").first.evaluate(
                            "node=>node.textContent='synthetic_permission_'.repeat(16)"
                        )
                    if template in {
                        "account_new.html",
                        "account_link.html",
                        "account_preview.html",
                        "account_operation.html",
                    }:
                        expect(page.locator(".task-steps li")).to_have_count(3)
                        expect(page.locator('.task-steps [aria-current="step"]')).to_have_count(1)
                    if template == "dashboard.html":
                        meters = page.locator(".permission-distribution meter")
                        assert meters.count() > 0
                        assert meters.evaluate_all(
                            "nodes=>nodes.reduce((sum,n)=>sum+n.value,0)===nodes[0].max"
                        )
                    if template == "account_detail.html":
                        expect(page.get_by_role("navigation", name="账号详情分区")).to_be_visible()

                    if template in {"employees.html", "permission_sets.html"}:
                        summary = page.locator(".relation-summary").first
                        expect(summary).to_contain_text(
                            "资料查阅角色" if template == "employees.html" else "查阅项目"
                        )
                        expect(summary).to_have_attribute("tabindex", "0")
                        expect(summary).to_have_attribute("data-tooltip", re.compile(".+"))
                    if template == "permissions.html":
                        expect(
                            page.get_by_role("columnheader", name="权限编码", exact=True)
                        ).to_be_visible()

                    unnamed = page.locator(
                        'main input:not([type="hidden"]),main select,main textarea'
                    ).evaluate_all(
                        "nodes=>nodes.filter(n=>n.getAttribute('aria-hidden')!=='true'&&!n.getAttribute('aria-label')&&!n.getAttribute('aria-labelledby')&&![...(n.labels||[])].some(label=>label.textContent.trim())).map(n=>n.name||n.id||n.tagName)"
                    )
                    assert not unnamed, (template, unnamed)
                    for width in (1440, 1024, 768, 390, 320):
                        page.set_viewport_size({"width": width, "height": 1000})
                        assert page.locator("html").evaluate(
                            "node=>node.scrollWidth<=innerWidth"
                        ), (template, width)
                        if width <= 390:
                            for table in page.locator(".management-table").all():
                                if table.is_visible():
                                    assert table.bounding_box()["width"] >= 680
                                    assert table.evaluate(
                                        "node => node.parentElement.tabIndex === 0 && node.parentElement.getAttribute('role') === 'region' && !!node.parentElement.getAttribute('aria-label')"
                                    ), template
                                    assert table.evaluate(
                                        "node => node.parentElement.previousElementSibling?.classList.contains('table-scroll-hint')"
                                    ), template
                                    assert table.evaluate(
                                        "node => getComputedStyle(node.parentElement.previousElementSibling).display === 'block'"
                                    ), template
                                    if width == 390:
                                        table.evaluate(
                                            "node => { node.parentElement.scrollLeft = 0; node.parentElement.focus(); }"
                                        )
                                        page.keyboard.press("ArrowRight")
                                        page.wait_for_function(
                                            "node => node.scrollLeft > 0",
                                            arg=table.locator("xpath=..").element_handle(),
                                        )
                                        table.evaluate(
                                            "node => { node.parentElement.scrollLeft = 0; node.parentElement.blur(); }"
                                        )
                        # 任何可滚动的表格容器都必须可聚焦，否则键盘用户无法读到被裁掉的内容。
                        # 只测 .management-table 会漏掉 .data-table 与无类裸 table（本轮补齐）。
                        if width <= 390:
                            wrappers = page.locator(".table-wrap").evaluate_all(
                                """nodes => nodes.filter(n => {
                                    const box = n.getBoundingClientRect();
                                    if (box.width === 0 && box.height === 0) return false;
                                    return true;
                                }).map(n => ({
                                    scrollable: n.scrollWidth > n.clientWidth,
                                    role: n.getAttribute('role'),
                                    tabIndex: n.tabIndex,
                                    label: n.getAttribute('aria-label'),
                                    table: n.querySelector('table')?.className || '(bare)'
                                }))"""
                            )
                            for wrapper in wrappers:
                                assert wrapper["role"] == "region", (template, wrapper)
                                assert wrapper["tabIndex"] == 0, (template, wrapper)
                                assert wrapper["label"], (template, wrapper)
                        if template in ("account_new.html", "account_link.html"):
                            expect(page.locator('[name="default_role"]')).to_have_count(0)
                            expect(page.locator("main")).to_contain_text("内置普通员工角色")
                        if template == "assistant_workspace.html":
                            page.evaluate("scrollTo(0,0)")
                            bounds = page.locator(".assistant-composer").bounding_box()
                            assert (
                                bounds
                                and bounds["y"] >= 0
                                and bounds["y"] + bounds["height"] <= 1000
                            ), (template, width, bounds)
                        if width in (1440, 390):
                            page.evaluate(
                                "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                            )
                            page.screenshot(
                                path=str(proof / f"{template}-{width}.png"), animations="disabled"
                            )
                        if template == "model_settings.html" and width == 390:
                            page.set_viewport_size({"width": 390, "height": 844})
                            # 折叠区里的控件即使不可见也有坐标，必须先展开再量几何，否则量到的是隐藏元素。
                            page.evaluate(
                                "() => document.querySelectorAll('main details').forEach(node => node.open = true)"
                            )
                            page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
                            page.evaluate(
                                "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                            )
                            # 浮动按钮固定在视口右下，滚动途中经过它下方是正常行为；判定点是滚到最底部时
                            # 是否仍有控件被压住——那才是用户无法靠继续滚动摆脱的遮挡。
                            blocked = page.evaluate("""() => {
                                const launch = document.querySelector('#assistant-launch');
                                if (!launch) return [];
                                const box = launch.getBoundingClientRect();
                                const atBottom = scrollY + innerHeight >= document.documentElement.scrollHeight - 1;
                                const hits = [];
                                for (const node of document.querySelectorAll('main button,main a,main input,main select,main textarea')) {
                                    if (node === launch || !node.checkVisibility()) continue;
                                    const rect = node.getBoundingClientRect();
                                    const area = Math.max(0, Math.min(rect.right, box.right) - Math.max(rect.left, box.left))
                                        * Math.max(0, Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top));
                                    if (area > 0) hits.push([node.tagName, node.id || node.name || node.className, Math.round(area), atBottom]);
                                }
                                return {atBottom, hits};
                            }""")
                            assert blocked["atBottom"], blocked
                            assert blocked["hits"] == [], blocked
                            # 浮层能压在内容上时，只有主区为它预留的高度保证「滚到底也读得到」。
                            # 几何检查在内容够长时可能天然不重叠，因此同时锁住这条结构性前提。
                            reserved = page.evaluate(
                                "() => parseFloat(getComputedStyle(document.querySelector('main')).paddingBottom)"
                            )
                            launch_height = page.evaluate(
                                "() => document.querySelector('#assistant-launch').getBoundingClientRect().height"
                            )
                            assert reserved >= launch_height + 40, (reserved, launch_height)
                            page.set_viewport_size({"width": 390, "height": 1000})
                    if template == "employees.html":
                        primary = page.locator(".page-header .button").first
                        brand_color = primary.evaluate(
                            "node => getComputedStyle(node).backgroundColor"
                        )
                        primary.hover()
                        hover_color = primary.evaluate(
                            "node => getComputedStyle(node).backgroundColor"
                        )
                        assert hover_color != brand_color
                        assert hover_color != "rgb(9, 94, 88)"
                        page.set_viewport_size({"width": 390, "height": 1000})
                        page.locator("#assistant-launch").click()
                        panel = page.locator("#assistant-panel")
                        expect(panel).to_have_attribute("aria-modal", "true")
                        assert page.locator("main").evaluate("node=>node.inert")
                        expect(panel.get_by_role("heading", name="助手当前不可用")).to_be_visible()
                        expect(
                            panel.get_by_role("button", name="了解我的权限", exact=True)
                        ).to_be_disabled()
                        expect(page.locator("#assistant-input-hint")).to_be_visible()
                        page.locator("#assistant-input").fill("未启用时起草，不自动发送")
                        last = panel.locator(".assistant-footnote button")
                        last.focus()
                        page.keyboard.press("Tab")
                        assert panel.evaluate("node=>node.contains(document.activeElement)")
                        page.set_viewport_size({"width": 1440, "height": 1000})
                        expect(page.locator("main")).to_have_js_property("inert", False)
                        assert panel.get_attribute("aria-modal") is None
                        expect(page.locator("#assistant-input")).to_have_value(
                            "未启用时起草，不自动发送"
                        )
                        page.set_viewport_size({"width": 390, "height": 1000})
                        expect(panel).to_have_attribute("aria-modal", "true")
                        page.locator("#assistant-input").fill("")
                        panel.get_by_role("button", name="收起智能助手").click()
                        expect(panel).to_be_hidden()
                        expect(page.locator("main")).to_have_js_property("inert", False)
                        expect(page.locator("#assistant-launch")).to_be_focused()
                    if template == "assistant_task.html":
                        expect(page.locator("#task-preview-summary")).to_contain_text("变更前")
                        expect(page.locator("#task-preview-summary")).to_contain_text("合成候选")
                        expect(page.get_by_text("完整服务器预览", exact=True)).to_be_visible()
                    page.emulate_media(reduced_motion="reduce")
                    if template != "assistant_workspace.html":
                        assert (
                            page.locator("#assistant-launch").evaluate(
                                "n=>getComputedStyle(n).transitionDuration"
                            )
                            == "0s"
                        )
                    assert (
                        page.locator(".account-trigger").evaluate(
                            "n=>getComputedStyle(n).transitionDuration"
                        )
                        == "0s"
                    )
                    page.emulate_media(reduced_motion="no-preference")
                    visited.append(template)
                assert set(visited) == templates
                assert not errors
                (proof / "result.json").write_text(
                    json.dumps(
                        {
                            "templates": visited,
                            "widths": [1440, 1024, 768, 390, 320],
                            "page_errors": errors,
                            "scope": "synthetic template presentation only",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
