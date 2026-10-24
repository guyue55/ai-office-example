"""逐页验收：页面清单闭合、PC布局、上下文筛选、普通表单草稿和完整页面出口。"""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from e2e_web_console import BASE, login
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    templates = ROOT / "office-data-service/app/entrypoints/web/templates"
    manifest = json.loads((ROOT / "tests/ui-pages.json").read_text())
    expected = {row["template"] for row in manifest["pages"]}
    actual = {p.name for p in templates.glob("*.html") if "{% extends" in p.read_text()}
    assert expected == actual, (expected - actual, actual - expected)
    components = {p.name for p in templates.iterdir() if p.is_file()} - expected
    assert components == set(manifest["components"]), components ^ set(manifest["components"])
    proof = ROOT / "runtime/foundation/page-review-ui"
    proof.mkdir(parents=True, exist_ok=True)
    visited = set()
    routes = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
            headless=True,
        )
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "dialog", lambda dialog: (errors.append("原生弹窗:" + dialog.type), dialog.dismiss())
        )
        login(page, "operator", (ROOT / "runtime/secrets/operator_password").read_text().strip())

        def inspect(template, path, status=200, suffix=""):
            response = page.goto(BASE + path)
            assert response.status == status, (template, response.status)
            expect(page.locator("main h2")).to_have_count(1)
            for width in (1024, 1280, 1440, 1920):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    template,
                    width,
                )
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.evaluate(
                "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
            )
            page.screenshot(path=str(proof / (template.removesuffix(".html") + suffix + ".png")))
            if page.evaluate("document.documentElement.scrollHeight > innerHeight + 50"):
                page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                page.screenshot(
                    path=str(proof / (template.removesuffix(".html") + suffix + "-bottom.png"))
                )
                page.evaluate("window.scrollTo(0, 0)")
            visited.add(template)
            routes.append({"template": template, "variant": suffix or "default", "status": status})

        for template, path in (
            ("dashboard.html", "/console/dashboard"),
            ("employees.html", "/console/employees"),
            ("roles.html", "/console/roles"),
            ("permission_sets.html", "/console/permission-sets"),
            ("permissions.html", "/console/permissions"),
            ("audit.html", "/console/audit"),
            ("client_authorizations.html", "/console/client-authorizations?scope=all"),
            ("account_operations.html", "/console/accounts/operations"),
            ("account_new.html", "/console/accounts/new"),
            ("account_link.html", "/console/accounts/link"),
            ("account_security.html", "/console/security"),
        ):
            inspect(template, path)
        inspect("account_link.html", "/console/accounts/link?q=employee", suffix="-search")
        page.goto(BASE + "/console/employees?q=本地测试员工")
        employee_path = page.locator(
            'main a[href^="/console/access/employees/"]'
        ).first.get_attribute("href")
        employee_id = employee_path.rsplit("/", 1)[1]
        page.goto(BASE + "/console/roles?q=普通员工")
        role_path = page.locator('main a[href^="/console/access/roles/"]').first.get_attribute(
            "href"
        )
        role_id = role_path.rsplit("/", 1)[1]
        page.goto(BASE + role_path)
        bundle_path = page.locator(
            'main a[href^="/console/access/permission-sets/"]'
        ).first.get_attribute("href")
        bundle_id = bundle_path.rsplit("/", 1)[1]
        for path, suffix in (
            (employee_path, "-employee"),
            (role_path, "-role"),
            (bundle_path, "-bundle"),
        ):
            inspect("access_detail.html", path, suffix=suffix)
        for kind, identifier, relation in (
            ("employees", employee_id, "roles"),
            ("roles", role_id, "permission-sets"),
            ("permission-sets", bundle_id, "permissions"),
        ):
            inspect(
                "links.html", f"/console/links/{kind}/{identifier}/{relation}", suffix="-" + kind
            )
        page.goto(BASE + "/console/accounts/operations")
        operation_path = page.locator("main td a").first.get_attribute("href")
        inspect("account_operation.html", operation_path)
        account_path = page.get_by_role("link", name="查看账号详情", exact=True).get_attribute(
            "href"
        )
        inspect("account_detail.html", account_path)
        # 从人员搜索进入真实账号抽屉，再沿历史入口进入完整页面。
        account_name = page.locator("main .card strong").first.inner_text()
        page.goto(BASE + "/console/employees?" + urlencode({"q": account_name}))
        page.locator(f'main a[href="{account_path}"]').first.click()
        expect(page.locator("#workspace")).to_be_visible()
        history = page.locator(
            '#workspace a[data-workspace-full][href*="/console/accounts/operations"]'
        )
        expect(history.first).to_be_visible()
        history.first.click()
        expect(page.locator("main h2")).to_have_text("账号处理进度")
        # 新建员工普通表单也必须保护草稿与完整页面出口。
        page.goto(BASE + "/console/employees")
        page.get_by_role("link", name="新建员工", exact=True).click()
        panel = page.locator("#workspace")
        name = panel.locator("input[name=name]")
        name.fill("仅验证未保存草稿")
        panel.locator("[data-workspace-close]").click()
        expect(page.locator("#action-confirm")).to_be_visible()
        page.locator("[data-confirm-cancel]").click()
        expect(name).to_have_value("仅验证未保存草稿")
        panel.locator("[data-workspace-full]").click()
        expect(page.locator("#action-confirm")).to_be_visible()
        page.locator("[data-confirm-cancel]").click()
        panel.locator("[data-workspace-close]").click()
        page.locator("[data-confirm-accept]").click()
        expect(panel).not_to_be_visible()
        assert page.evaluate("""() => {
            const event = new Event('beforeunload', {cancelable: true});
            window.dispatchEvent(event);
            return !event.defaultPrevented;
        }"""), "已确认放弃且关闭的抽屉不得继续阻断导航"
        # 完整页面提交本表单不弹“放弃修改”；仅核对预览，不创建账号。
        page.goto(BASE + "/console/accounts/new")
        page.locator("main input[name=name]").fill("仅核对未提交员工")
        username = "review-preview-" + uuid4().hex[:8]
        page.locator("main input[name=username]").fill(username)
        assert page.locator("main form").evaluate(
            "form => window.OfficeForms.dirty(document.querySelector('main')) && !window.OfficeForms.dirty(document.querySelector('main'), form)"
        )
        page.get_by_role("button", name="核对开通信息", exact=True).click()
        expect(page.locator("main h2")).to_have_text("核对账号操作")
        expect(page.locator("main")).to_contain_text(username)
        expect(page.locator("#action-confirm")).not_to_be_visible()
        visited.add("account_preview.html")
        routes.append(
            {"template": "account_preview.html", "variant": "post-preview-only", "status": 200}
        )
        # 重置筛选保留人员上下文、范围与每页数量，清除查询、日期与页码。
        page.goto(BASE + f"/console/accounts/operations?identifier={employee_id}&q=不匹配&limit=20")
        page.locator(".filter-reset").click()
        query = parse_qs(urlsplit(page.url).query)
        assert query == {"identifier": [employee_id], "limit": ["20"]}, query
        page.goto(BASE + "/console/client-authorizations?scope=all&q=不匹配&limit=20")
        page.locator(".filter-reset").click()
        assert parse_qs(urlsplit(page.url).query) == {"scope": ["all"], "limit": ["20"]}
        page.goto(BASE + "/console/permissions?q=不存在的权限")
        expect(page.locator("main")).to_contain_text("没有符合条件的权限")
        inspect("error.html", "/console/roles?limit=bad", status=422)
        inspect("signed_out.html", "/console/signed-out")
        assert visited == expected - {"account_credential.html"}, expected - visited
        assert not errors, errors
        (proof / "coverage.json").write_text(
            json.dumps(
                {
                    "visited": sorted(visited),
                    "variants": routes,
                    "security_templates": ["account_preview.html", "account_credential.html"],
                    "security_suite": "e2e_accounts.py",
                    "widths": [1024, 1280, 1440, 1920],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        browser.close()
    print("ALL_PAGES_REVIEW_E2E_OK")


if __name__ == "__main__":
    main()
