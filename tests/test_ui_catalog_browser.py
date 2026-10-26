"""实际模板的全页面巡视；合成上下文，不能替代权限／身份／数据库功能测试。"""

import json
import os
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
from ui_catalog import context

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[1]


def test_all_page_templates_at_desktop_and_narrow_widths():
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅隔离模板上下文")
    inventory = json.loads((ROOT / "tests/ui-pages.json").read_text())
    templates = {row["template"] for row in inventory["pages"]}
    app = FastAPI()

    @app.get("/catalog/{template}")
    async def catalog(request: Request, template: str):
        assert template in templates
        value = context(template)
        # 模板根据路径避免重复挂载完整工作区；此代理仅改变渲染上下文。
        path = (
            "/console/assistant/workspace"
            if template == "assistant_workspace.html"
            else "/console/catalog"
        )
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
                for template in sorted(templates):
                    response = page.goto(base + "/catalog/" + template)
                    assert response.status == 200, template
                    expect(page.locator("main h2")).to_have_count(1)
                    expect(page.locator("#assistant-panel")).to_have_count(1)
                    expect(page.locator(".shell-nav")).to_have_count(1)
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
                        if template == "account_link.html":
                            checkbox = page.locator('[name="default_role"]')
                            bounds = checkbox.bounding_box()
                            assert bounds and bounds["width"] <= 24, (template, width, bounds)
                            assert (
                                checkbox.evaluate(
                                    "node=>getComputedStyle(node.parentElement).display"
                                )
                                == "flex"
                            )
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
