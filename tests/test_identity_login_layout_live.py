"""目标实例的匿名登录页布局；只开空白表单，不填写或读取任何凭证。"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.live


def test_anonymous_identity_login_layout_and_reload_stability():
    base = os.environ.get("OFFICE_UI_PUBLIC_URL", "").rstrip("/")
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1" or not base:
        pytest.skip("需显式OFFICE_UI_PUBLIC_URL和浏览器开关；只读取指定实例公开登录页")
    target = urlsplit(base)
    assert target.scheme in {"http", "https"} and not target.username and not target.password
    proof = Path(__file__).resolve().parents[1] / "runtime/agent-research/identity-ui"
    proof.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
        )
        try:
            # 新上下文不借用用户浏览器，不安装身份数据，不读取已有会话。
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(base + "/console")
            expect(page.locator("#username")).to_be_visible()
            expect(page.locator("#password")).to_have_value("")
            destination = urlsplit(page.url)
            assert destination.netloc == target.netloc
            assert destination.path.startswith("/identity/realms/")
            title = page.locator("#kc-page-title")
            assert re.search(r"[\u4e00-\u9fff]", title.inner_text())
            positions = []
            for _ in range(3):
                page.reload()
                expect(page.locator("#username")).to_be_visible()
                page.evaluate("() => document.fonts.ready")
                page.evaluate(
                    "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                )
                positions.append(round(title.bounding_box()["y"]))
            assert max(positions) - min(positions) <= 1
            for width in (1440, 1024, 768, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.locator("html").evaluate("node=>node.scrollWidth<=innerWidth")
                for selector in ("#username", "#password", "#kc-login"):
                    box = page.locator(selector).bounding_box()
                    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
                assert page.locator("#username").input_value() == ""
                assert page.locator("#password").input_value() == ""
                if width in (1440, 390):
                    page.screenshot(path=str(proof / f"login-{width}.png"), animations="disabled")
        finally:
            browser.close()
