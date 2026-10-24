"""统一列表PC验收：紧凑筛选、数字分页、查询条件与无脚本交互。"""

import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from e2e_web_console import BASE, login
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    proof = ROOT / "runtime/foundation/tables-ui"
    proof.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
            headless=True,
        )
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, "operator", (ROOT / "runtime/secrets/operator_password").read_text().strip())
        paths = (
            "employees",
            "roles",
            "permission-sets",
            "permissions",
            "audit",
            "client-authorizations",
            "accounts/operations",
        )
        for path in paths:
            page.goto(BASE + "/console/" + path)
            expect(page.locator(".pagination-numbered")).to_have_count(1)
            expect(page.locator(".page-summary")).to_contain_text("共")
            expect(page.locator("select[name=limit]")).to_have_value("10")
            for width in (1024, 1280, 1440, 1920):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
            page.set_viewport_size({"width": 1440, "height": 1000})
            if path in ("audit", "client-authorizations"):
                expect(page.locator(".filter-advanced")).not_to_have_attribute("open")
                bounds = page.locator(".list-filters").bounding_box()
                assert bounds and bounds["height"] < 120, bounds
                page.wait_for_timeout(150)
                page.screenshot(
                    path=str(proof / (path + ".png")), full_page=True, animations="disabled"
                )
        page.goto(BASE + "/console/roles?q=分页验收&limit=10")
        expect(page.locator(".page-summary")).to_contain_text("共")
        page.get_by_role("link", name="下一页", exact=True).click()
        expect(page.locator('.pagination-numbered [aria-current="page"]')).to_have_text("2")
        assert parse_qs(urlsplit(page.url).query)["q"] == ["分页验收"]
        page.locator("input[type=search][name=q]").fill("不存在的筛选结果")
        page.get_by_role("button", name="搜索", exact=True).click()
        expect(page.locator('.pagination-numbered [aria-current="page"]')).to_have_text("1")
        expect(page.locator(".page-summary")).to_contain_text("0")
        page.goto(BASE + "/console/audit")
        page.locator(".filter-advanced>summary").focus()
        page.keyboard.press("Enter")
        expect(page.locator(".list-filters input[name=actor]")).to_be_visible()
        page.locator(".list-filters input[name=actor]").fill("不匹配的操作者")
        page.locator("select[name=action]").select_option("session.revoke")
        page.locator(".list-filters input[name=since]").fill("2026-10-01")
        page.locator(".list-filters input[name=until]").fill("2026-10-31")
        page.get_by_role("button", name="查询", exact=True).click()
        expect(page.locator(".filter-advanced")).to_have_attribute("open", "")
        expect(page.locator(".list-filters input[name=actor]")).to_have_value("不匹配的操作者")
        expect(page.locator(".page-summary")).to_contain_text("0")
        page.locator(".filter-reset").click()
        expect(page.locator(".filter-advanced")).not_to_have_attribute("open")
        page.locator("select[name=limit]").select_option("20")
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("select[name=limit]")).to_have_value("20")
        page.locator("input[name=page]").fill("2")
        page.get_by_role("button", name="跳转", exact=True).click()
        expect(page.locator('.pagination-numbered [aria-current="page"]')).to_have_text("2")
        fallback = browser.new_context(storage_state=ctx.storage_state(), java_script_enabled=False)
        basic = fallback.new_page()
        basic.goto(BASE + "/console/audit")
        basic.locator(".filter-advanced>summary").click()
        basic.locator(".list-filters input[name=actor]").fill("无脚本筛选")
        basic.get_by_role("button", name="查询", exact=True).click()
        expect(basic.locator(".page-summary")).to_contain_text("0")
        assert not errors, errors
        browser.close()
    print("UNIFIED_TABLES_E2E_OK")


if __name__ == "__main__":
    main()
