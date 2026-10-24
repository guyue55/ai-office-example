"""PC真实浏览器：授权筛选、取消确认、撤销、跨端失效、归档与无脚本回退。"""

import os
from pathlib import Path

from e2e_web_console import BASE, login
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    proof = ROOT / "runtime/foundation/client-authorizations-ui"
    proof.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
            headless=True,
        )
        admin = browser.new_context(viewport={"width": 1440, "height": 1000})
        employee = browser.new_context(viewport={"width": 1440, "height": 1000})
        page, other = admin.new_page(), employee.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, "operator", (ROOT / "runtime/secrets/operator_password").read_text().strip())
        login(other, "employee", (ROOT / "runtime/secrets/employee_password").read_text().strip())
        other.goto(BASE + "/console/client-authorizations")
        current = other.locator("tbody tr").filter(has=other.get_by_text("当前登录", exact=True))
        expect(current).to_have_count(1)
        identifier = current.locator("code").text_content().strip()
        assert other.locator("select[name=limit]").input_value() == "10"
        assert other.locator("select[name=scope] option").all_text_contents() == ["我的授权"]
        page.goto(BASE + "/console/client-authorizations?scope=all&q=" + identifier)
        row = page.locator("tbody tr")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("OAuth 浏览器授权")
        for width in (1024, 1280, 1440, 1920):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1440, "height": 1000})
        expect(page.locator(".brand")).to_have_count(1)
        page.wait_for_timeout(200)
        page.screenshot(
            path=str(proof / "authorization-list.png"), full_page=True, animations="disabled"
        )
        row.locator("summary[aria-label=停用授权]").click()
        form = page.locator("#record-dialog form")
        expect(row.locator("summary[aria-label=归档记录]")).to_be_visible()
        expect(row.locator("summary[aria-label=管理授权]")).to_have_count(0)
        expect(form.locator("input[name=action]")).to_have_value("disable")
        form.locator("input[name=reason]").fill("浏览器验收停用指定授权")
        form.get_by_role("button", name="停用授权").click()
        expect(page.locator("#action-confirm")).to_be_visible()
        page.get_by_role("button", name="取消", exact=True).click()
        expect(row).to_contain_text("未停用")
        form.get_by_role("button", name="停用授权").click()
        page.screenshot(
            path=str(proof / "authorization-confirm.png"), full_page=True, animations="disabled"
        )
        page.locator("[data-confirm-accept]").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator(".notice")).to_contain_text("业务访问已停止")
        page.goto(BASE + "/console/client-authorizations?scope=all&q=" + identifier)
        expect(page.locator("tbody tr")).to_contain_text("已停用")
        expect(page.locator("tbody tr")).to_contain_text("身份登录已结束")
        other.goto(BASE + "/console/dashboard")
        other.wait_for_load_state("domcontentloaded")
        expect(other.locator("main")).to_contain_text("登录授权已结束")
        other.get_by_role("link", name="重新登录控制台").click()
        expect(other.locator("#username")).to_be_visible()
        row = page.locator("tbody tr")
        row.locator("summary[aria-label=归档记录]").click()
        form = page.locator("#record-dialog form")
        expect(form.locator("input[name=action]")).to_have_value("archive")
        form.locator("input[name=reason]").fill("归档保留审计验收")
        form.get_by_role("button", name="归档记录").click()
        page.locator("[data-confirm-accept]").click()
        # 确认弹窗异步启动保存；等待业务成功回执，不能用旧页面的空闲状态代替完成。
        expect(page.locator(".notice")).to_contain_text("业务访问已停止")
        expect(page.locator("tbody")).to_contain_text("没有符合条件")
        page.goto(BASE + "/console/client-authorizations?scope=all&q=" + identifier)
        expect(page.locator("tbody")).to_contain_text("没有符合条件")
        page.locator(".filter-advanced > summary").click()
        page.locator("select[name=archived]").select_option("true")
        page.get_by_role("button", name="查询", exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("tbody")).to_contain_text("已归档")
        expect(page.locator("select[name=archived]")).to_have_value("true")
        assert page.locator("input[type=search][name=q]").input_value() == identifier
        basic = browser.new_context(storage_state=admin.storage_state(), java_script_enabled=False)
        fallback = basic.new_page()
        fallback.goto(BASE + "/console/client-authorizations")
        expect(fallback.locator("h2").first).to_have_text("客户端授权")
        fallback.locator("select[name=limit]").select_option("10")
        fallback.get_by_role("button", name="应用", exact=True).click()
        expect(fallback.locator("select[name=limit]")).to_have_value("10")
        assert not errors, errors
        browser.close()
    print("CLIENT_AUTHORIZATIONS_E2E_OK")


if __name__ == "__main__":
    main()
