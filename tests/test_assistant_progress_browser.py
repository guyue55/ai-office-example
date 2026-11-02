"""合成BFF下验收真实SSE交付、原位DOM与侧栏/全页复用。"""

import os
import socket
import threading
from pathlib import Path

import pytest
import uvicorn
from app.entrypoints.web.responses import instance_cookie
from playwright.sync_api import expect, sync_playwright
from test_assistant_workspace_web import install_workspace
from test_web_console import web  # noqa: F401

pytestmark = pytest.mark.live


def test_progress_updates_in_place_and_does_not_resubmit(web):  # noqa: F811
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("显式隔离浏览器验收")
    conversation, turn, calls, _state = install_workspace(web)
    conversation["title"] = "权限与操作范围核查（隔离验收）"
    turn["prompt"] = "请说明我当前具备哪些权限，以及可以进行哪些操作。"
    turn.update(
        status="running",
        pending=None,
        progress={
            "schema_version": 1,
            "seq": 1,
            "elapsed_ms": 200,
            "stage": "reply",
            "text": "",
            "steps": [{"id": "1", "operation": "whoami", "status": "completed", "elapsed_ms": 30}],
            "truncated": False,
        },
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(web.app, log_level="critical", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        with sync_playwright() as browser_api:
            browser = browser_api.chromium.launch(channel=os.getenv("OFFICE_TEST_BROWSER_CHANNEL"))
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies(
                [
                    {
                        "name": instance_cookie(
                            "office_web_session", web.application.settings.public_url
                        ),
                        "value": web.cookie,
                        "url": f"http://127.0.0.1:{port}",
                    }
                ]
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/console/assistant/workspace")
            page.get_by_role("button", name="历史会话", exact=True).click()
            page.locator(f'[data-conversation="{conversation["id"]}"]').click()
            expect(page.locator("[data-progress-text]")).to_be_hidden()
            turn["progress"] = {**turn["progress"], "seq": 2, "text": "正在整理"}
            expect(page.locator("[data-progress-text]")).to_have_text("正在整理")
            page.locator('[data-run="task-one"]').evaluate("el=>el.dataset.marker='stable'")
            turn["progress"] = {**turn["progress"], "seq": 3, "text": "已读取权限，正在整理结果"}
            expect(page.locator("[data-progress-text]")).to_have_text("已读取权限，正在整理结果")
            expect(page.locator('[data-run="task-one"]')).to_have_attribute("data-marker", "stable")
            page.locator("[data-progress] summary").click()
            expect(page.locator("[data-progress-steps]")).to_contain_text("查看本人身份与权限")
            assert page.locator("body").evaluate("el=>el.scrollWidth<=innerWidth")
            evidence = Path("runtime/conversation-events/browser")
            evidence.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(evidence / "streaming.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator("body").evaluate("el=>el.scrollWidth<=innerWidth")
            page.set_viewport_size({"width": 1440, "height": 1000})
            assert any(name == "agent.get" for name, _ in calls)
            assert not any(name == "agent.start" for name, _ in calls)
            turn.update(
                status="completed",
                version=4,
                result={
                    "explanation": "你可查询获准数据。涉及修改时，我会先展示预览，等待你确认。",
                    "receipts": [
                        {
                            "operation": "whoami",
                            "status": "read",
                            "result": {
                                "name": "隔离测试员工",
                                "permissions": ["project.read"],
                                "permission_details": [
                                    {
                                        "name": "查看项目",
                                        "group": "项目与数据",
                                        "description": "查看获准数据容器及字段定义",
                                    }
                                ],
                            },
                        }
                    ],
                },
            )
            expect(page.locator("#assistant-messages")).to_contain_text("你可查询获准数据。")
            expect(page.locator("[data-progress-text]")).to_be_hidden()
            # 终态后重绘及重新连接必须仍能定位复用节点。
            page.get_by_role("button", name="更多会话操作", exact=True).click()
            page.get_by_role("button", name="同步会话状态", exact=True).click()
            expect(page.locator("[data-progress-text]")).to_be_hidden()
            assert not errors
            page.get_by_text("工具回执（1条）", exact=True).click()
            expect(page.locator(".assistant-receipt")).to_contain_text("隔离测试员工")
            folder = Path("runtime/conversation-events/browser")
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / "conversation.png"), full_page=True)
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
