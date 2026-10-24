"""真实 PC 页面开通：默认角色、明确取消默认、内置标记与授予结果。"""

import os
from uuid import uuid4

from e2e_web_console import BASE, ROOT, login
from playwright.sync_api import expect, sync_playwright


def main():
    proof = ROOT / "runtime/foundation/access-ui/provisioning"
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
        for default in (True, False):
            page.goto(BASE + "/console/employees")
            page.get_by_text("高级：直接绑定登录标识", exact=True).click()
            dialog = page.locator("#workspace")
            expect(dialog).to_be_visible()
            name = "浏览器开通验证-" + uuid4().hex[:10]
            dialog.locator("input[name=name]").fill(name)
            # 仅构造业务绑定，不声称创建了身份产品中的可登录账号。
            dialog.locator("input[name=subject]").fill("ui-provision-" + uuid4().hex)
            check = dialog.locator("input[name=assign_default_role]")
            expect(check).to_be_checked()
            if not default:
                check.uncheck()
            page.screenshot(
                path=str(proof / ("default-form.png" if default else "optout-form.png")),
                animations="disabled",
            )
            dialog.get_by_role("button", name="绑定并开通", exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(page).to_have_url(BASE + "/console/employees")
            search = page.get_by_role("search")
            search.locator("input[name=q]").fill(name)
            search.get_by_role("button", name="搜索", exact=True).click()
            row = page.locator("tr").filter(has_text=name)
            expect(row).to_have_count(1)
            expect(row).to_contain_text("已关联 1 个角色" if default else "未配置角色")
            row.get_by_role("link", name="查看授权详情", exact=True).click()
            expect(dialog).to_be_visible()
            expect(dialog).to_contain_text(name)
            if default:
                expect(page.get_by_role("link", name="普通员工 ↗", exact=True)).to_be_visible()
                for label in ("查看项目与字段", "上报项目数据", "查看本人数据", "维护本人数据"):
                    expect(dialog.get_by_text(label, exact=True)).to_be_visible()
            else:
                expect(dialog).to_contain_text("尚未关联角色")
            page.screenshot(
                path=str(proof / ("default-detail.png" if default else "optout-detail.png")),
                animations="disabled",
            )
        page.goto(BASE + "/console/roles?q=普通员工")
        expect(page.locator("main")).to_contain_text("内置 · 新员工默认")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        browser.close()
    print("PROVISIONING_E2E_OK")


if __name__ == "__main__":
    main()
