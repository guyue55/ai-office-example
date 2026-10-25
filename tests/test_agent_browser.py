"""独立本机HTTP+Chromium验证助手页面及统一确认弹窗；不使用现有登录。"""

import os
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright
from test_web_console import web  # noqa: F401 -- 复用显式隔离身份与Web应用。

pytestmark = pytest.mark.live


def test_assistant_real_browser_confirmation_layout_and_escape(web):  # noqa: F811
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅启动隔离本机应用和浏览器")
    decisions = []
    task = {
        "id": "isolated-task",
        "version": 1,
        "prompt": "查看项目并上报候选 <script>evil()</script>",
        "status": "awaiting_confirmation",
        "updated_at": datetime.now(UTC),
        "cancel_requested": False,
        "pending": {
            "ticket": "isolated-ticket",
            "operation": "submit_record",
            "arguments": {"values": {"note": "<img src=x>"}},
            "preview": {"operation": "新增候选", "after": {"备注": "本人确认前不写入"}},
        },
        "result": None,
    }

    async def operation(principal, name, payload, trace, **options):
        if name == "me":
            return {"name": "隔离浏览器测试员工", "permissions": ["agent.run"]}
        if name == "agent.list":
            return {
                "items": [task],
                "next": None,
                "has_more": False,
                "pagination": {
                    "total": 1,
                    "page_size": 10,
                    "page": 1,
                    "pages": 1,
                    "start": 1,
                    "end": 1,
                },
            }
        if name == "agent.capabilities":
            return {"ready": True, "message": ""}
        if name == "agent.decide":
            decisions.append(payload)
            task["pending"]["decision"] = payload["approve"]
        return task

    web.application.call.side_effect = operation
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(web.app, log_level="critical", lifespan="off"))
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
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                base = f"http://127.0.0.1:{port}"
                page.context.add_cookies(
                    [{"name": "office_web_session", "value": web.cookie, "url": base}]
                )
                page.goto(base + "/console/assistant")
                expect(page.get_by_role("heading", name="智能助手", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="开始处理")).to_be_visible()
                assert (
                    page.locator("script").evaluate_all(
                        "els => els.some(e => e.textContent.includes('evil()'))"
                    )
                    is False
                )
                page.get_by_role("link", name="查看任务").click()
                expect(page.get_by_role("heading", name="需要您确认的操作")).to_be_visible()
                page.get_by_label("您的决定").select_option("approve")
                page.get_by_role("button", name="提交决定").click()
                expect(page.locator("#action-confirm")).to_be_visible()
                assert not decisions
                page.locator("[data-confirm-cancel]").click()
                assert not decisions
                page.get_by_role("button", name="提交决定").click()
                page.locator("[data-confirm-accept]").click()
                expect(
                    page.get_by_text("决定已记录或停止请求已提交，等待执行器处理。", exact=True)
                ).to_be_visible()
                assert len(decisions) == 1 and decisions[0]["approve"] is True
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors
                proof = Path("runtime/agent-research/browser")
                proof.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(proof / "assistant-task.png"), full_page=True)
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
