"""隔离页面实际颜色和长列表巡视；不使用真实身份或业务数据。"""

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright
from test_web_console import web  # noqa: F401

pytestmark = pytest.mark.live


def contrast(first, second):
    def luminance(rgb):
        values = [float(value.strip()) / 255 for value in rgb[4:-1].split(",")]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def test_rendered_contrast_focus_and_long_employee_list(web):  # noqa: F811
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅隔离浏览器／数据夹具")
    original = web.application.call.side_effect
    calls = []

    async def operation(principal, name, payload, trace):
        if name == "access.list" and payload["kind"] == "employees":
            page = payload.get("page", 1)
            calls.append(page)
            return {
                "items": [
                    {
                        "id": f"person-{index}",
                        "name": "测试人员与跨部门协作的较长显示名称" + str(index),
                        "login_name": "synthetic-" + "long-identifier-" * 7 + str(index),
                        "active": index % 2 == 0,
                        "version": 1,
                    }
                    for index in range((page - 1) * 10, min(page * 10, 23))
                ],
                "next": None,
                "has_more": page < 3,
                "pagination": {
                    "page": page,
                    "page_size": 10,
                    "total": 23,
                    "pages": 3,
                    "start": (page - 1) * 10 + 1,
                    "end": min(page * 10, 23),
                },
            }
        return await original(principal, name, payload, trace)

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
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies(
                    [{"name": "office_web_session", "value": web.cookie, "url": base}]
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/console/employees")
                queue = page.locator(".pending-queue")
                expect(queue).not_to_have_attribute("open", "")
                expect(queue.locator("summary")).to_contain_text("1 人")
                expect(page.locator(".flow-guide")).not_to_have_attribute("open", "")
                expect(page.locator("a.permission-name")).to_have_count(10)
                expect(page.locator("a.permission-name").first).to_be_in_viewport()

                def color(selector, prop):
                    return page.locator(selector).first.evaluate(
                        "(n,p)=>getComputedStyle(n)[p]", prop
                    )

                muted = color(".page-header .muted", "color")
                assert contrast(muted, color("body", "backgroundColor")) >= 4.5
                assert contrast(muted, "rgb(255, 255, 255)") >= 4.5
                field = page.locator("input[type=search]").first
                assert (
                    contrast(
                        field.evaluate("n=>getComputedStyle(n).borderTopColor"),
                        "rgb(255, 255, 255)",
                    )
                    >= 3
                )
                field.focus()
                assert (
                    contrast(
                        field.evaluate("n=>getComputedStyle(n).outlineColor"), "rgb(255, 255, 255)"
                    )
                    >= 3
                )
                page.locator(".shell-nav > a").first.focus()
                assert (
                    contrast(
                        color(".shell-nav > a", "outlineColor"),
                        color(".shell-nav", "backgroundColor"),
                    )
                    >= 3
                )
                page.locator(".account-trigger").click()
                page.locator(".account-panel .account-action").first.focus()
                assert (
                    contrast(
                        color(".account-panel .account-action", "outlineColor"),
                        "rgb(255, 255, 255)",
                    )
                    >= 3
                )
                page.locator(".page-header h2").click()
                # 详情以共享抽屉导入时，不得复制全局助手入口和位置栏。
                page.locator("a.permission-name").first.click()
                expect(page.locator("#workspace")).to_be_visible()
                expect(page.locator("#workspace .workspace-section-title")).to_be_attached()
                expect(page.locator("#workspace #assistant-launch")).to_have_count(0)
                expect(page.locator("#workspace .shell-context")).to_have_count(0)
                page.get_by_role("button", name="关闭操作面板", exact=True).click()
                queue.locator("summary").click()
                expect(queue.get_by_role("button", name="认领并开通", exact=True)).to_be_visible()
                queue.locator("summary").click()
                page.locator(".flow-guide > summary").click()
                expect(page.locator(".flow")).to_be_visible()
                page.locator(".flow-guide > summary").click()
                page.get_by_role("link", name="下一页", exact=True).click()
                assert calls[-1] == 2
                expect(page.locator("a.permission-name").first).to_contain_text("10")
                for width in (1440, 1024, 768, 375, 320):
                    page.set_viewport_size({"width": width, "height": 900})
                    assert page.locator("body").evaluate("n=>n.scrollWidth<=innerWidth")
                # 窄屏导航按需展开，不能继续吞掉整个首屏；Esc回到菜单入口。
                page.set_viewport_size({"width": 390, "height": 800})
                navigation = page.locator(".shell-nav")
                toggle = page.locator("[data-navigation-toggle]")
                expect(navigation).to_be_hidden()
                toggle.click()
                expect(navigation).to_be_visible()
                expect(toggle).to_have_attribute("aria-expanded", "true")
                page.keyboard.press("Escape")
                expect(navigation).to_be_hidden()
                expect(toggle).to_be_focused()
                # 不执行脚本仍可访问全部获准导航，增强不能成为访问前提。
                plain_context = browser.new_context(
                    java_script_enabled=False, viewport={"width": 390, "height": 800}
                )
                plain_context.add_cookies(
                    [{"name": "office_web_session", "value": web.cookie, "url": base}]
                )
                plain = plain_context.new_page()
                plain.goto(base + "/console/employees")
                expect(plain.locator(".shell-nav")).to_be_visible()
                expect(
                    plain.locator(".shell-nav").get_by_role("link", name="仪表盘", exact=True)
                ).to_be_visible()
                plain_context.close()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(base + "/console/employees")
                evidence = Path("runtime/agent-research/visual-refinement")
                evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(evidence / "employees.png"), full_page=True)
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()
