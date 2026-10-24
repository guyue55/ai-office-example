"""PC 管理面板活体验证：真实登录，抽屉修改恢复、预览、弹窗与失败保护。"""

import os
from contextlib import contextmanager
from html.parser import HTMLParser
from urllib.parse import urlencode

from e2e_web_console import BASE, ROOT, login
from playwright.sync_api import expect, sync_playwright


class FormInputs(HTMLParser):
    """仅解析测试页的表单字段，不输出 CSRF 或会话资料。"""

    def __init__(self, target=None):
        super().__init__()
        self.target = target
        self.collecting = target is None
        self.fields = {}
        self.selected = set()

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "form" and self.target:
            self.collecting = fields.get("action") == self.target
        if tag != "input" or not self.collecting:
            return
        name, value = fields.get("name"), fields.get("value")
        if name and value:
            self.fields[name] = value
            if fields.get("type") == "checkbox" and "checked" in fields:
                self.selected.add(value)

    def handle_endtag(self, tag):
        if tag == "form" and self.target:
            self.collecting = False


@contextmanager
def restore_name(page, path, name, active=True):
    try:
        yield
    finally:
        response = page.request.get(BASE + "/console/employees?" + urlencode({"q": name}))
        assert response.ok
        parsed = FormInputs(target=path)
        parsed.feed(response.text())
        assert "version" in parsed.fields
        data = {key: parsed.fields[key] for key in ("csrf", "version")}
        data.update(name=name)
        if active:
            data["active"] = "on"
        response = page.request.post(
            BASE + path,
            data=urlencode(data),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.ok


@contextmanager
def restore_roles(page, path, baseline):
    """即使断言失败，也通过真实 BFF 与最新版本恢复专用测试员工原关联。"""
    try:
        yield
    finally:
        response = page.request.get(BASE + path)
        assert response.ok
        parsed = FormInputs()
        parsed.feed(response.text())
        if parsed.selected != baseline:
            data = {
                "csrf": parsed.fields["csrf"],
                "version": parsed.fields["version"],
                **{"id_" + value: value for value in baseline},
            }
            response = page.request.post(
                BASE + path,
                data=urlencode(data),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert response.ok


def main():
    password = (ROOT / "runtime/secrets/operator_password").read_text().strip()
    proof = ROOT / "runtime/foundation/access-ui/workspace"
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
        login(page, "operator", password)
        # 员工已超过一页，用查询参数定位测试人员，不依赖其出现在首页
        page.goto(BASE + "/console/employees?" + urlencode({"q": "本地测试员工"}))
        row = page.locator("tr").filter(has_text="本地测试员工")
        expect(row).to_have_count(1)
        row.get_by_role("link", name="授权角色", exact=True).click()
        panel = page.locator("#workspace")
        expect(panel).to_be_visible()
        expect(panel.locator("#grant-form")).to_be_visible()
        assert page.url.startswith(BASE + "/console/employees")
        # 编辑角色时查看候选内容，返回保留尚未保存的选择。
        baseline = set(
            panel.locator("input[type=checkbox]:checked").evaluate_all(
                "elements => elements.map(el => el.value)"
            )
        )
        path = row.get_by_role("link", name="授权角色", exact=True).get_attribute("href")
        with restore_roles(page, path, baseline):
            selected = panel.locator("input[type=checkbox]:not(:checked)").first
            expect(selected).to_be_visible()
            original_id = selected.input_value()
            selected.check()
            expect(panel.locator("#grant-status")).to_contain_text("1 项未保存")
            panel.locator("[data-workspace-preview]").first.click()
            expect(panel.locator("[data-workspace-back]")).to_be_visible()
            panel.locator("[data-workspace-back]").click()
            expect(panel.locator(f'input[value="{original_id}"]')).to_be_checked()
            # Escape 及关闭必须保护未保存编辑，取消后仍留在面板。
            panel.locator("[data-workspace-close]").click()
            page.locator("[data-confirm-cancel]").click()
            expect(panel).to_be_visible()
            page.screenshot(path=str(proof / "role-drawer.png"), animations="disabled")
            panel.get_by_role("button", name="保存授权", exact=True).click()
            expect(panel.locator("#workspace-feedback")).to_contain_text("已保存")
            expect(panel.locator(f'input[value="{original_id}"]')).to_be_checked()
            # 同一真实入口恢复原关联。
            panel.locator(f'input[value="{original_id}"]').uncheck()
            panel.get_by_role("button", name="保存授权", exact=True).click()
            expect(panel.locator("#workspace-feedback")).to_contain_text("已保存")
            panel.locator("[data-workspace-close]").click()
            expect(panel).not_to_be_visible()
            expect(page.locator("main")).to_contain_text("本地测试员工")

        # 抽屉相对翻页、搜索仍以授权页为基准，不能跳回背后的人员列表。
        row.get_by_role("link", name="授权角色", exact=True).click()
        expect(panel.locator("#grant-form")).to_be_visible()
        pager = panel.get_by_role("link", name="下一页", exact=True)
        if pager.count():
            original_page = page.url
            pager.click()
            expect(panel.locator("#workspace-feedback")).to_have_text("")
            expect(panel.locator("#grant-form")).to_be_visible()
            assert page.url == original_page
        search = panel.get_by_role("search")
        search.locator("input[name=q]").fill("不会匹配的角色")
        search.get_by_role("button", name="搜索", exact=True).click()
        expect(panel.locator("tbody")).to_contain_text("没有符合条件的选项")
        expect(panel.locator("input[type=checkbox]:checked").first).to_be_visible()
        panel.locator("[data-workspace-close]").click()

        # 短表单弹窗，不需要先创建才可取消。
        page.goto(BASE + "/console/roles")
        page.get_by_role("link", name="新建角色", exact=True).click()
        expect(panel).to_have_class("workspace is-compact")
        expect(panel.locator("input[name=name]")).to_be_focused()
        page.screenshot(path=str(proof / "create-role-modal.png"), animations="disabled")
        page.keyboard.press("Escape")
        expect(page.locator("a[data-open-form]")).to_be_focused()
        # 角色与权限集两条关联沿用相同抽屉。
        page.locator('a[href^="/console/links/roles/"]').first.click()
        expect(panel.locator("#grant-form")).to_be_visible()
        panel.locator("[data-workspace-close]").click()
        page.goto(BASE + "/console/permission-sets")
        page.locator('a[href^="/console/links/permission-sets/"]').first.click()
        expect(panel.locator("#grant-form")).to_be_visible()
        page.screenshot(path=str(proof / "permissions-drawer.png"), animations="disabled")
        panel.locator("[data-workspace-close]").click()

        # 只测试取消停用，不修改管理员或其他员工状态。
        page.goto(BASE + "/console/employees?" + urlencode({"q": "本地测试员工"}))
        row = page.locator("tr").filter(has_text="本地测试员工")
        row.get_by_role("button", name="停用", exact=True).click()
        confirmation = page.locator("#action-confirm")
        expect(confirmation).to_be_visible()
        expect(confirmation).to_contain_text("本地测试员工")
        confirmation.get_by_role("button", name="取消", exact=True).click()
        expect(row.get_by_role("button", name="停用", exact=True)).to_be_visible()

        # 确认后真实停用与重新启用，检查主列表与成功反馈；异常也恢复测试账号。
        status_path = row.locator("form[data-confirm-message]").get_attribute("action")
        with restore_name(page, status_path, "本地测试员工"):
            row.get_by_role("button", name="停用", exact=True).click()
            confirmation.get_by_role("button", name="确认停用", exact=True).click()
            expect(row.get_by_role("button", name="启用", exact=True)).to_be_visible()
            expect(page.locator("#ui-notifications .notice")).to_contain_text("人员信息已更新")
            row.get_by_role("button", name="启用", exact=True).click()
            confirmation.get_by_role("button", name="确认启用", exact=True).click()
            expect(row.get_by_role("button", name="停用", exact=True)).to_be_visible()
        page.reload()

        # 姓名弹窗实际保存，并在 finally 通过最新版本恢复专用测试人员姓名。
        name_path = row.locator("form[data-confirm-message]").get_attribute("action")
        with restore_name(page, name_path, "本地测试员工"):
            row.locator("summary", has_text="修改姓名").click()
            expect(page.locator("#record-dialog input[name=name]")).to_have_value("本地测试员工")
            page.locator("#record-dialog input[name=name]").fill("本地测试员工·姓名验证")
            page.locator("#record-dialog").get_by_role(
                "button", name="保存姓名", exact=True
            ).click()
            expect(page.locator("#record-dialog")).not_to_be_visible()
            expect(page.locator("main")).to_contain_text("本地测试员工·姓名验证")
        page.reload()

        # 写入失败保留草稿，禁止错误提示伪装成功。
        row.get_by_role("link", name="授权角色", exact=True).click()
        expect(panel.locator("#grant-form")).to_be_visible()
        choice = panel.locator("input[type=checkbox]").first
        before = choice.is_checked()
        choice.set_checked(not before)
        version = panel.locator("[name=version]")
        current_version = version.input_value()
        version.evaluate("(el) => el.value = String(Number(el.value) - 1)")
        panel.get_by_role("button", name="保存授权", exact=True).click()
        expect(panel.locator("#workspace-feedback")).to_contain_text("配置已更新")
        expect(choice).to_be_checked(checked=not before)
        version.evaluate("(el, value) => el.value = value", current_version)
        page.route(
            "**/console/links/**",
            lambda route: route.abort() if route.request.method == "POST" else route.continue_(),
        )
        panel.get_by_role("button", name="保存授权", exact=True).click()
        expect(panel.locator("#workspace-feedback")).to_contain_text("结果暂时无法确认")
        expect(choice).to_be_checked(checked=not before)
        page.unroute("**/console/links/**")
        choice.set_checked(before)
        panel.locator("[data-workspace-close]").click()
        # 无动画偏好与窄屏仍可操作。
        page.emulate_media(reduced_motion="reduce")
        page.set_viewport_size({"width": 390, "height": 844})
        row.get_by_role("link", name="授权角色", exact=True).click()
        expect(panel).to_be_visible()
        assert panel.evaluate("el => getComputedStyle(el).animationName") == "none"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        panel.locator("[data-workspace-close]").click()
        assert not errors, errors
        # 无 JavaScript 时仍走完整页面，不把关键操作绑死在增强脚本上。
        fallback = browser.new_context(
            storage_state=context.storage_state(), java_script_enabled=False
        )
        native_page = fallback.new_page()
        native_page.goto(BASE + "/console/employees?" + urlencode({"q": "本地测试员工"}))
        native_page.locator("tr").filter(has_text="本地测试员工").get_by_role(
            "link", name="授权角色", exact=True
        ).click()
        assert "/console/links/employees/" in native_page.url
        expect(native_page.locator("#grant-form")).to_be_visible()
        fallback.close()
        context.close()
        browser.close()
    print("WORKSPACE_E2E_OK")


if __name__ == "__main__":
    main()
