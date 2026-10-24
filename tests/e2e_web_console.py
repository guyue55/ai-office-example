#!/usr/bin/env python3
"""Web 控制台端到端浏览器验证：Keycloak 登录 → 页面渲染 → 权限关联编辑闭环。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
from live_environment import SERVICE_URL

BASE = SERVICE_URL


def login(page, user: str, pw: str) -> None:
    """访问 /console，若被重定向到 Keycloak 则完成授权码 + PKCE 登录。"""
    # 页面可用性以DOM及后续可见元素断言为准，不依赖浏览器全局网络空闲。
    page.goto(f"{BASE}/console")
    page.wait_for_load_state("domcontentloaded")
    complete_login(page, user, pw)


def complete_login(page, user: str, pw: str) -> None:
    """完成已打开的登录事务，不重新访问入口覆盖待验证的状态。"""
    if "realms/office" in page.url or ":8081" in page.url:
        if page.locator("#username").count():
            page.locator("#username").fill(user)
        page.locator("#password").fill(pw)
        page.locator("#kc-login").click()
        page.wait_for_load_state("domcontentloaded")


def save_links(page, selector):
    """等待服务端版本变化，避免异步保存后立即读取旧页面或并发覆盖。"""
    version = page.locator("#grant-form input[name=version]").input_value()
    page.locator(selector).click()
    expect(page.locator("#grant-form input[name=version]")).not_to_have_value(version)


def main() -> None:
    op_pw = (ROOT / "runtime/secrets/operator_password").read_text().strip()
    proof = ROOT / "runtime/foundation/access-ui"
    proof.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
            headless=True,
        )
        for order in ((0, 1), (1, 0)):
            concurrent = browser.new_context()
            try:
                pages = [concurrent.new_page(), concurrent.new_page()]
                # 先创建两个真实应用授权事务，再分别进入身份产品；
                # 仍覆盖Cookie互相覆盖的原反例，同时避免提交另一页已被SSO作废的旧表单。
                starts = [concurrent.request.get(f"{BASE}/console", max_redirects=0) for _ in pages]
                assert all(start.status == 303 for start in starts)
                for index in order:
                    pages[index].goto(starts[index].headers["location"])
                    pages[index].wait_for_load_state("domcontentloaded")
                    complete_login(pages[index], "operator", op_pw)
                    # 一页完成SSO后，Keycloak可能将另一旧表单标为过期；
                    # 使用身份产品原生“继续登录”链接，保留原事务，不重开应用登录。
                    continuation = pages[index].locator("a[href*='/login-actions/authenticate?']")
                    if continuation.count() == 1:
                        continuation.click()
                        pages[index].wait_for_load_state("domcontentloaded")
                    expect(pages[index].locator("main h2")).to_have_text("仪表盘")
            finally:
                concurrent.close()
        print("MULTI_TAB_LOGIN_OK")
        ctx = browser.new_context()
        page = ctx.new_page()

        # 1) 未登录访问 → 302/303 → Keycloak → 回跳 dashboard
        login(page, "operator", op_pw)
        assert "/console" in page.url and "8081" not in page.url, f"登录后仍在 {page.url}"
        expect(page.locator("body")).to_contain_text("本地测试operator")
        expect(page.get_by_text("上报项目数据", exact=True)).to_be_visible()
        # PC 常用窗口宽度：主要页面不应出现整页横向溢出。
        for width in (1024, 1280, 1440, 1920):
            page.set_viewport_size({"width": width, "height": 900})
            for path in ("dashboard", "employees", "roles", "permission-sets", "permissions"):
                page.goto(f"{BASE}/console/{path}")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                    f"PC 页面溢出：{path}, {width}"
                )
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{BASE}/console/dashboard")
        page.screenshot(path=str(proof / "dashboard.png"), full_page=True)

        # 2) 页面渲染检查
        for path, marker in [
            ("/console/employees", "人员管理"),
            ("/console/roles", "角色"),
            ("/console/permission-sets", "权限集"),
            ("/console/permissions", "权限目录"),
            ("/console/audit", "审计记录"),
        ]:
            page.goto(f"{BASE}{path}")
            expect(page.locator("body")).to_contain_text(marker)

        # 3) 从人员管理页解析「授权角色」链接，进入授权关联页
        page.goto(f"{BASE}/console/employees")
        page.wait_for_load_state("domcontentloaded")
        page.screenshot(path=str(proof / "employees.png"), full_page=True)
        # 员工已超过一页，先按姓名筛选，避免依赖目标员工恰好出现在首页
        page.get_by_role("search").locator("input[name=q]").fill("本地测试员工")
        page.get_by_role("search").get_by_role("button", name="搜索", exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        # 选普通员工（非授权管理员），避免触发“必须保留可用授权管理员”保护
        employee_row = page.locator("tr").filter(has_text=re.compile("本地测试(员工|employee)"))
        role_link = employee_row.locator("a", has_text="授权角色")
        links_href = role_link.get_attribute("href")
        page.goto(f"{BASE}{links_href}")
        expect(page.locator("body")).to_contain_text("授权关联")
        # 将所有已选角色筛到候选页之外，验证外部关联控件仍会随表单提交。
        page.get_by_role("search").locator("input[name=q]").fill("不存在的角色-" + uuid4().hex)
        page.get_by_role("search").get_by_role("button", name="搜索", exact=True).click()
        expect(page.locator("body")).to_contain_text("其他页或筛选外已选择")
        # 外置选项也必须触发未保存保护；取消搜索或离页不能丢失修改。
        guard_choice = page.locator("input[type=checkbox][name^='id_']:checked").first
        guard_id = guard_choice.input_value()
        guard_choice = page.locator(f'input[type=checkbox][value="{guard_id}"]')
        guard_choice.uncheck()
        expect(page.locator("#grant-status")).to_contain_text("1 项未保存")

        page.get_by_role("search").get_by_role("button", name="搜索", exact=True).click()
        page.locator("[data-confirm-cancel]").click()
        expect(guard_choice).not_to_be_checked()
        page.get_by_role("link", name="查看详情与配置效果", exact=True).click()
        page.locator("[data-confirm-cancel]").click()
        expect(guard_choice).not_to_be_checked()
        guard_choice.check()
        expect(page.locator("#grant-status")).to_contain_text("没有未保存")
        guard_choice.uncheck()
        page.get_by_role("search").get_by_role("button", name="搜索", exact=True).click()
        page.locator("[data-confirm-accept]").click()
        expect(guard_choice).to_be_checked()
        expect(page.locator("#grant-status")).to_contain_text("没有未保存")

        # 4) 权限关联编辑闭环：取消一个已勾选权限集 → 保存 → 验证变化
        def selected(target):
            return set(
                target.locator(
                    "input[name^='id_']:checked, input[type=hidden][name^='id_']"
                ).evaluate_all("els => els.map(e => e.value)")
            )

        save = 'form[action^="/console/links/"] button[type="submit"]'
        before = selected(page)
        assert before, "专用测试员工应已有角色"
        stale = ctx.new_page()
        stale.goto(f"{BASE}{links_href}")
        removed = page.locator("input[type=checkbox][name^='id_']:checked").first
        removed_id = removed.input_value()
        removed_name = removed.locator("..").inner_text().strip()
        try:
            removed.uncheck()
            save_links(page, save)
            expect(page.locator("main h2")).to_have_text("授权关联")
            assert selected(page) == before - {removed_id}
            # 旧页面不能覆盖刚刚生效的授权。
            with stale.expect_response(lambda r: r.request.method == "POST") as result:
                stale.locator(save).click()
            assert result.value.status == 409
            expect(stale.locator("body")).to_contain_text("操作未成功")
        finally:
            stale.close()
            page.goto(f"{BASE}{links_href}")
            page.get_by_role("search").locator("input[name=q]").fill(removed_name)
            page.get_by_role("search").get_by_role("button", name="搜索", exact=True).click()
            page.locator(f'input[type=checkbox][value="{removed_id}"]').check()
            save_links(page, save)
            assert selected(page) == before, "必须按原始角色ID完整恢复"

        # 权限集编辑真实链路；只变更新建的无关联测试权限集。
        page.goto(f"{BASE}/console/permission-sets")
        name = "浏览器验证-" + uuid4().hex[:8]
        page.get_by_role("link", name="新建权限集", exact=True).click()
        page.locator('form[action="/console/permission-sets/new"] input[name=name]').fill(name)
        page.locator('form[action="/console/permission-sets/new"] button').click()
        expect(page.locator("#workspace #grant-form")).to_be_visible()
        page.goto(page.locator("[data-workspace-full]").get_attribute("href"))
        expect(page.locator("main h2")).to_have_text("授权关联")
        expect(page.locator("body")).to_contain_text(name)
        bundle_id = urlsplit(page.url).path.split("/")[-2]
        search = page.get_by_role("search")
        search.locator("input[name=q]").fill("record.create")
        search.get_by_role("button", name="搜索", exact=True).click()
        box = page.locator('input[type=checkbox][value="record.create"]')
        box.check()
        save_links(page, save)
        expect(box).to_be_checked()
        page.screenshot(path=str(proof / "permission-editor.png"), full_page=True)

        # 创建后进入下一步：权限集配置 → 角色关联 → 双向详情与实时效果。
        page.goto(f"{BASE}/console/roles")
        page.get_by_role("link", name="新建角色", exact=True).click()
        role_name = name + "角色"
        page.locator('form[action="/console/roles/new"] input[name=name]').fill(role_name)
        page.locator('form[action="/console/roles/new"] button').click()
        expect(page.locator("#workspace #grant-form")).to_be_visible()
        page.goto(page.locator("[data-workspace-full]").get_attribute("href"))
        expect(page.locator("main h2")).to_have_text("授权关联")
        role_id = urlsplit(page.url).path.split("/")[-2]
        search = page.get_by_role("search")
        search.locator("input[name=q]").fill(name)
        search.get_by_role("button", name="搜索", exact=True).click()
        page.locator(f'input[type=checkbox][value="{bundle_id}"]').check()
        save_links(page, save)
        page.goto(f"{BASE}/console/access/roles/{role_id}")
        expect(page.locator("main h2")).to_have_text(role_name)
        expect(page.get_by_text("上报项目数据", exact=True)).to_be_visible()
        page.goto(f"{BASE}/console/access/permission-sets/{bundle_id}")
        expect(page.locator("body")).to_contain_text(role_name)
        page.screenshot(path=str(proof / "permission-detail.png"), full_page=True)
        detail_link = page.get_by_role("link", name="查看角色授权详情", exact=True)
        page.goto(BASE + detail_link.get_attribute("href"))
        expect(page.locator("main h2")).to_have_text(role_name)
        # 修改共享权限集后，角色详情立即反映撤销；不调整真实员工授权。
        page.goto(f"{BASE}/console/links/permission-sets/{bundle_id}/permissions")
        search = page.get_by_role("search")
        search.locator("input[name=q]").fill("record.create")
        search.get_by_role("button", name="搜索", exact=True).click()
        box = page.locator('input[type=checkbox][value="record.create"]')
        box.uncheck()
        save_links(page, save)
        expect(box).not_to_be_checked()
        page.goto(f"{BASE}/console/access/roles/{role_id}")
        expect(page.get_by_text("上报项目数据", exact=True)).to_have_count(0)
        print("ACCESS_WORKFLOW_OK")

        page.goto(f"{BASE}/console/dashboard")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator("nav.shell-nav").count() == 1
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(proof / "dashboard-mobile.png"))
        page.locator("#my-permissions").scroll_into_view_if_needed()
        expect(page.get_by_text("上报项目数据", exact=True)).to_be_visible()
        page.screenshot(path=str(proof / "permissions-mobile.png"))
        page.set_viewport_size({"width": 1280, "height": 900})

        # 普通员工无管理权限；页面入口与直达写入同样受后端限制。
        employee_ctx = browser.new_context()
        employee_page = employee_ctx.new_page()
        login(
            employee_page,
            "employee",
            (ROOT / "runtime/secrets/employee_password").read_text().strip(),
        )
        csrf = employee_page.locator('form[action="/auth/logout"] input[name=csrf]').input_value()
        denied = employee_page.request.post(
            f"{BASE}/console/roles/new", form={"csrf": csrf, "name": "不得创建"}
        )
        assert denied.status == 403 and "403001" in denied.text()
        response = employee_page.goto(f"{BASE}/console/employees")
        assert response is not None and response.status == 403
        expect(employee_page.locator("body")).to_contain_text("操作未成功")
        employee_ctx.close()

        # 6) 审计页应含本次授权变更记录（access.changed）
        page.goto(f"{BASE}/console/audit")
        expect(page.locator("body")).to_contain_text("access.changed")

        # 7) 退出登录 → 回到未认证
        page.locator("[data-account-menu] > summary").click()
        page.locator('form[action="/auth/logout"] button').click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("body")).to_contain_text("已退出控制台")
        expect(page.locator("body")).to_contain_text("对应身份登录均已结束")
        # 保留浏览器全部 Cookie，验证退出确实结束 SSO，而非测试替用户清理。
        page.reload()
        for path in ("/console", "/console/"):
            page.goto(BASE + path)
            expect(page.locator("#username")).to_be_visible()
        page.goto(f"{BASE}/console")
        assert "8081" in page.url or "realms/office" in page.url, "logout 后应重新跳 Keycloak"

        browser.close()
        print("E2E_OK")


if __name__ == "__main__":
    main()
