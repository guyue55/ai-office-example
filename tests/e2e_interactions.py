"""PC共享交互验收：操作图标、悬浮说明、模态详情、焦点与稳定布局。"""

import os
from pathlib import Path

from e2e_web_console import BASE, login
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    proof = ROOT / "runtime/foundation/interactions-ui"
    proof.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
            headless=True,
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # 站内操作不应触发浏览器原生确认；系统级离页提醒单独保留。
        page.on(
            "dialog",
            lambda dialog: (errors.append("非预期浏览器弹窗:" + dialog.type), dialog.dismiss()),
        )
        login(page, "operator", (ROOT / "runtime/secrets/operator_password").read_text().strip())
        for path in (
            "employees",
            "roles",
            "permission-sets",
            "permissions",
            "audit",
            "client-authorizations?scope=all",
            "accounts/operations",
        ):
            page.goto(BASE + "/console/" + path)
            for width in (1024, 1280, 1440, 1920):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
            page.set_viewport_size({"width": 1440, "height": 1000})
            actions = page.locator("main .icon-action")
            for action in actions.all():
                assert action.get_attribute("aria-label")
                expect(action.locator("svg")).to_have_count(1)
                bounds = action.bounding_box()
                assert bounds and bounds["width"] >= 32 and bounds["height"] >= 32
            summary = page.locator("tbody details > summary").first
            if summary.count():
                summary.scroll_into_view_if_needed()
                table = summary.locator("xpath=ancestor::table[1]")
                before = table.bounding_box()
                summary.click()
                modal = page.locator("#record-dialog")
                expect(modal).to_be_visible()
                assert before == table.bounding_box(), path
                expect(page.locator("#record-dialog-title")).to_be_focused()
                if path == "audit":
                    page.screenshot(path=str(proof / "audit-detail-dialog.png"), full_page=True)
                page.get_by_role("button", name="关闭详情", exact=True).click()
                expect(modal).not_to_be_visible()
                expect(summary).to_be_focused()
            if actions.count():
                action = actions.first
                action.hover()
                expect(page.locator("#ui-tooltip")).to_be_visible()
                expect(page.locator("#ui-tooltip")).to_have_text(action.get_attribute("aria-label"))
                page.locator("#ui-tooltip").hover()
                expect(page.locator("#ui-tooltip")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator("#ui-tooltip")).not_to_be_visible()
            page.screenshot(
                path=str(proof / (path.split("?")[0].replace("/", "-") + ".png")), full_page=True
            )

        page.goto(BASE + "/console/employees")
        trigger = page.locator("summary[aria-label=修改姓名]").first
        trigger.click()
        field = page.locator("#record-dialog input[name=name]")
        field.fill(field.input_value() + "·未保存")
        page.get_by_role("button", name="关闭详情", exact=True).click()
        expect(page.locator("#action-confirm")).to_be_visible()
        page.locator("[data-confirm-cancel]").click()
        expect(page.locator("#record-dialog")).to_be_visible()
        page.get_by_role("button", name="关闭详情", exact=True).click()
        page.locator("[data-confirm-accept]").click()
        expect(page.locator("#record-dialog")).not_to_be_visible()
        expect(trigger).to_be_focused()
        # 通知固定在角落，不改变页面滚动；错误不会自动消失。
        page.evaluate(
            "window.scrollTo(0, 120); window.OfficeUI.notify('操作成功验证'); window.OfficeUI.notify('操作失败验证', true)"
        )
        success = page.get_by_role("status").filter(has_text="操作成功验证")
        failure = page.get_by_role("alert").filter(has_text="操作失败验证")
        expect(success).to_be_visible()
        expect(failure).to_be_visible()
        assert (
            page.locator("#ui-notifications").evaluate("e => getComputedStyle(e).position")
            == "fixed"
        )
        success.hover()
        page.wait_for_timeout(8200)
        expect(success).to_be_visible()
        expect(failure).to_be_visible()
        page.emulate_media(reduced_motion="reduce")
        assert success.evaluate("e => getComputedStyle(e).animationName") == "none"
        success.get_by_role("button", name="关闭通知").click()
        expect(success).to_have_count(0)
        assert not errors, errors
        # 无脚本仍保留原始文案和原生详情，不能只剩不可理解的图标。
        basic = browser.new_context(
            storage_state=context.storage_state(), java_script_enabled=False
        )
        fallback = basic.new_page()
        fallback.goto(BASE + "/console/employees")
        fallback.locator("summary", has_text="修改姓名").first.click()
        expect(fallback.locator("tbody input[name=name]").first).to_be_visible()
        browser.close()
    print("INTERACTIONS_E2E_OK")


if __name__ == "__main__":
    main()
