"""全站账号入口：PC/窄屏布局、键盘、原生降级及带 CSRF 的退出。"""

import os

from e2e_web_console import BASE, ROOT, login
from playwright.sync_api import expect, sync_playwright


def main():
    proof = ROOT / "runtime/foundation/access-ui/account-menu"
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
        login(page, "operator", (ROOT / "runtime/secrets/operator_password").read_text().strip())
        menu = page.locator("[data-account-menu]")
        trigger = menu.locator("summary")
        logout = menu.get_by_role("button", name="退出登录", exact=True)
        expect(menu).to_have_count(1)
        expect(logout).not_to_be_visible()
        for height in (768, 900, 1080):
            page.set_viewport_size({"width": 1440, "height": height})
            bounds = trigger.bounding_box()
            assert bounds and 10 <= height - bounds["y"] - bounds["height"] <= 16, bounds
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        page.screenshot(path=str(proof / "closed.png"), animations="disabled")
        # 展开不得移动账号行：曾因全局 details[open]>summary 的 20px 间距整行上移。
        before = trigger.bounding_box()
        trigger.click()
        expect(logout).to_be_visible()
        after = trigger.bounding_box()
        assert before and after and before["y"] == after["y"], (before, after)
        assert trigger.evaluate("el => getComputedStyle(el).marginBottom") == "0px"
        # 中性双箭头表示“可选菜单”，不再用单向上箭头或旋转表达方向。
        assert menu.locator(".account-chevron path").count() == 2
        trigger.click()
        expect(logout).not_to_be_visible()
        trigger.focus()
        page.keyboard.press("Enter")
        expect(logout).to_be_visible()
        page.keyboard.press("Tab")
        expect(menu.get_by_role("link", name="我的权限")).to_be_focused()
        page.keyboard.press("Tab")
        expect(menu.get_by_role("link", name="账号与安全")).to_be_focused()
        page.keyboard.press("Tab")
        expect(menu.get_by_role("link", name="我的客户端授权")).to_be_focused()
        page.keyboard.press("Tab")
        expect(logout).to_be_focused()
        page.keyboard.press("Escape")
        expect(logout).not_to_be_visible()
        expect(trigger).to_be_focused()
        page.keyboard.press("Space")
        expect(logout).to_be_visible()
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        expect(menu.get_by_role("link", name="账号与安全")).to_be_focused()
        page.keyboard.press("Tab")
        expect(menu.get_by_role("link", name="我的客户端授权")).to_be_focused()
        page.keyboard.press("Tab")
        expect(logout).to_be_focused()
        page.keyboard.press("Tab")
        expect(logout).not_to_be_visible()
        trigger.click()
        page.get_by_role("heading", name="仪表盘", exact=True).click()
        expect(logout).not_to_be_visible()
        trigger.click()
        menu.get_by_role("link", name="我的权限").click()
        expect(page).to_have_url(BASE + "/console/dashboard#my-permissions")
        expect(page.locator("#my-permissions")).to_be_in_viewport()
        # 菜单在其他页面共用，长姓名仅在紧凑入口省略，展开后完整可见。
        page.goto(BASE + "/console/employees")
        page.locator(".account-name").evaluate(
            "el => el.textContent = '用于检验布局的较长业务账号名称'.repeat(4)"
        )
        for width in (1024, 1280, 1440, 1920, 390):
            page.set_viewport_size({"width": width, "height": 900})
            trigger.click()
            expect(logout).to_be_visible()
            bounds = menu.locator(".account-panel").bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["y"] >= 0
            assert bounds["x"] + bounds["width"] <= width
            assert bounds["y"] + bounds["height"] <= 900
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            if width == 1440:
                page.screenshot(path=str(proof / "expanded.png"), animations="disabled")
            page.keyboard.press("Escape")
        page.emulate_media(reduced_motion="reduce")
        assert trigger.evaluate("el => getComputedStyle(el).transitionDuration") == "0s"
        # 禁用脚本也可展开与退出；保留受控 POST，不改成 GET 或客户端清令牌。
        fallback = browser.new_context(
            storage_state=context.storage_state(), java_script_enabled=False
        )
        basic = fallback.new_page()
        basic.goto(BASE + "/console/dashboard")
        basic.locator("[data-account-menu] > summary").click()
        basic.get_by_role("button", name="退出登录", exact=True).click()
        expect(basic.locator("body")).to_contain_text("已退出控制台")
        assert not errors, errors
        browser.close()
    print("ACCOUNT_MENU_E2E_OK")


if __name__ == "__main__":
    main()
