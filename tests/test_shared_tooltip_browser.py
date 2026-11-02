"""独立浏览器验证共享提示的对象归属；不接业务服务或用户会话。"""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.live


def test_tooltip_focus_hover_ownership_and_dismissal():
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1，仅使用独立浏览器")
    root = Path(__file__).resolve().parents[1] / "office-data-service/app/entrypoints/web/templates"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 700})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_content("""<button id="a" data-tooltip="提示A">A</button>
                <button id="b" data-tooltip="提示B">B</button><button id="outside">普通操作</button>
                <dialog id="record-dialog"><h2 id="record-dialog-title" tabindex="-1"></h2>
                <button data-record-close>关闭</button><div id="record-dialog-content"></div></dialog>
                <div id="ui-notifications"></div><div id="ui-tooltip" popover="manual" role="tooltip"></div>""")
            page.add_style_tag(content=(root / "styles.css").read_text())
            page.add_script_tag(content=(root / "interactions.js").read_text())
            tip = page.get_by_role("tooltip")
            page.locator("#a").focus()
            expect(tip).to_have_text("提示A")
            page.locator("#b").hover()
            expect(tip).to_have_text("提示B")
            expect(page.locator("#a")).to_be_focused()
            page.mouse.move(600, 500)
            expect(tip).to_have_text("提示A")  # B离开后不能因A聚焦而把B留住。
            tip.hover()
            page.wait_for_timeout(220)  # 经过隐藏延迟，悬停提示自身仍保持。
            expect(tip).to_have_text("提示A")
            page.keyboard.press("Escape")
            page.evaluate("dispatchEvent(new Event('scroll'));dispatchEvent(new Event('resize'))")
            page.wait_for_timeout(220)
            expect(tip).to_be_hidden()
            page.mouse.move(600, 500)
            page.locator("#outside").focus()
            page.locator("#a").focus()
            expect(tip).to_have_text("提示A")
            page.locator("#outside").focus()
            expect(tip).to_be_hidden()
            page.locator("#a").focus()
            page.keyboard.press("Escape")  # 待显示阶段也能关闭。
            page.wait_for_timeout(220)
            expect(tip).to_be_hidden()
            page.locator("#b").hover()
            expect(tip).to_have_text("提示B")
            assert not errors
        finally:
            browser.close()
