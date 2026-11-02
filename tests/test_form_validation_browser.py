"""共享表单反馈：只验证隔离页面，不提交账号操作或使用真实凭证。"""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.live


def test_form_feedback_retains_values_and_restores_descriptions():
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅隔离浏览器")
    root = Path(__file__).resolve().parents[1] / "office-data-service/app/entrypoints/web/templates"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
        )
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 800})
            page.set_content("""<form method="post">
                <label for="name">姓名</label><input id="name" name="name" required>
                <label for="username">登录账号
                <input id="username" name="username" pattern="[a-z0-9]{2,10}" aria-describedby="hint"></label>
                <p id="hint">填写两至十位字母或数字</p>
                <button type="submit">核对信息</button><button type="reset">重置</button></form>""")
            page.add_style_tag(content=(root / "styles.css").read_text())
            page.add_style_tag(content=(root / "shell.css").read_text())
            page.add_script_tag(content=(root / "form-validation.js").read_text())
            page.locator("#username").fill("含 空格")
            page.get_by_role("button", name="核对信息").click()
            summary = page.locator(".form-errors")
            expect(summary).to_be_focused()
            expect(summary.locator("li")).to_have_count(2)
            expect(page.locator("#username")).to_have_value("含 空格")
            expect(page.locator("#username")).to_have_attribute("aria-invalid", "true")
            assert "hint" in page.locator("#username").get_attribute("aria-describedby").split()
            summary.get_by_role("button", name="请填写姓名。", exact=True).click()
            expect(page.locator("#name")).to_be_focused()
            page.locator("#name").fill("测试人员")
            expect(summary.locator("li")).to_have_count(1)
            page.get_by_role("button", name="核对信息").click()
            expect(page.locator(".form-errors")).to_have_count(1)
            expect(page.locator(".field-error")).to_have_count(1)
            expect(summary.get_by_role("button")).to_have_text("请按填写说明检查登录账号。")
            page.locator("#username").fill("person01")
            expect(summary).to_have_count(0)
            expect(page.locator("#username")).to_have_attribute("aria-describedby", "hint")
            expect(page.locator("#username")).not_to_have_attribute("aria-invalid", "true")
            expect(page.locator("#name")).to_have_value("测试人员")
            page.locator("#username").fill("非法")
            page.get_by_role("button", name="核对信息").click()
            page.get_by_role("button", name="重置", exact=True).click()
            expect(page.locator(".form-errors")).to_have_count(0)
            expect(page.locator(".field-error")).to_have_count(0)
        finally:
            browser.close()
