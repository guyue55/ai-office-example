"""真实浏览器开户、凭证准备、首次改密、日常操作与撤销；秘密只留进程内存。"""

import os
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from uuid import uuid4

import httpx
from conftest import MemoryStore
from e2e_web_console import BASE, ROOT, complete_login, login
from office_client import OfficeClient
from office_client.product import PRODUCT
from playwright.sync_api import expect, sync_playwright


def main():
    proof = ROOT / "runtime/foundation/access-ui/accounts"
    proof.mkdir(parents=True, exist_ok=True)
    username = "ui-account-" + uuid4().hex[:10]
    operator_password = (ROOT / "runtime/secrets/operator_password").read_text().strip()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
            headless=True,
        )
        admin = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = admin.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, "operator", operator_password)
        page.goto(BASE + "/console/employees")
        page.get_by_role("link", name="新建员工", exact=True).click()
        expect(page.locator("#workspace")).to_have_class(re.compile("is-compact"))
        print("OPEN_CREATE_OK", flush=True)
        page.locator("#workspace input[name=name]").fill("真实浏览器开户验证")
        page.locator("#workspace input[name=username]").fill(username)
        expect(page.locator("#workspace input[name=default_role]")).to_be_checked()
        page.screenshot(path=str(proof / "create-form.png"), animations="disabled")
        page.get_by_role("button", name="核对开通信息").click()
        expect(page.locator("main")).to_contain_text("普通员工")
        page.get_by_role("button", name="确认并提交").click()
        operation_url = page.url
        expect(page.get_by_role("button", name="验证本人身份")).to_be_visible(timeout=20000)
        page.screenshot(path=str(proof / "credential-pending.png"))
        page.get_by_role("button", name="验证本人身份").click()
        complete_login(page, "operator", operator_password)
        expect(page.get_by_role("button", name="生成并显示一次")).to_be_visible()
        with page.expect_response(
            lambda r: r.url.endswith("/credential") and r.request.method == "POST"
        ) as response:
            page.get_by_role("button", name="生成并显示一次").click()
        assert "no-store" in response.value.headers["cache-control"]
        password = page.locator("#temporary-password").input_value()
        assert len(password) > 24
        # 不对含明文密码的页面截图或保存HTML。
        page.get_by_role("link", name="查看开通结果").click()
        expect(page.locator("main")).to_contain_text("处理完成", timeout=20000)
        detail_url = BASE + page.get_by_role("link", name="查看账号详情").get_attribute("href")
        page.get_by_role("link", name="查看账号详情").click()
        print("CREATE_AND_CREDENTIAL_OK", flush=True)
        expect(page.locator("main")).to_contain_text(username)
        page.screenshot(path=str(proof / "account-detail.png"))

        employee = browser.new_context(viewport={"width": 1440, "height": 1000})
        worker = employee.new_page()
        login(worker, username, password)
        expect(worker.locator("#password-new")).to_be_visible()
        new_password = secrets.token_urlsafe(22) + "Aa1!"
        worker.locator("#password-new").fill(new_password)
        worker.locator("#password-confirm").fill(new_password)
        worker.locator("#kc-submit").click()
        expect(worker.locator("main h2")).to_have_text("仪表盘")
        expect(worker.locator("main")).to_contain_text("上报项目数据")
        worker.screenshot(path=str(proof / "first-login.png"))
        print("FIRST_LOGIN_OK", flush=True)
        # 本人改密使用原生动作，取消不能被显示为已修改。
        worker.goto(BASE + "/console/security")
        worker.get_by_role("button", name="修改本人密码").click()
        complete_login(worker, username, new_password)
        expect(worker.locator("#password-new")).to_be_visible()
        changed_password = secrets.token_urlsafe(22) + "Aa1!"
        worker.locator("#password-new").fill(changed_password)
        worker.locator("#password-confirm").fill(changed_password)
        worker.locator("#kc-submit").click()
        expect(worker.locator("main h2")).to_have_text("账号与安全")
        expect(worker.locator("main")).to_contain_text("不将回跳视为密码已修改的证明")

        # 新浏览器用新密码登录，避免SSO掩盖改密没有生效。
        probe_context = browser.new_context()
        probe = probe_context.new_page()
        login(probe, username, changed_password)
        expect(probe.locator("main h2")).to_have_text("仪表盘")
        probe_context.close()
        urls, token_store = Queue(), MemoryStore()
        client = OfficeClient(BASE, store=token_store, browser_open=urls.put)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.oauth.login, 60)
            worker.goto(urls.get(timeout=10))
            worker.wait_for_url("http://127.0.0.1:8765/callback**", timeout=15000)
            future.result(timeout=10)
        assert client.me()["name"] == "真实浏览器开户验证"
        old_tokens = token_store.load().copy()
        # 普通员工持有有效令牌仍不能读取他人账号或发起账号管理。
        denied = httpx.get(
            BASE + "/api/v2/accounts/search",
            params={"q": username},
            headers={"Authorization": "Bearer " + old_tokens["access_token"]},
        )
        assert denied.status_code == 403
        worker.goto(BASE + "/console/dashboard")
        print("SELF_PASSWORD_OK", flush=True)
        for kind in ("结束全部登录", "停用账号", "启用账号", "重置密码"):
            page.goto(detail_url)
            page.get_by_role("button", name=kind, exact=True).click()
            page.get_by_role("button", name="确认并提交").click()
            if kind == "重置密码":
                expect(page.get_by_role("button", name="验证本人身份")).to_be_visible(timeout=20000)
                page.get_by_role("button", name="验证本人身份").click()
                complete_login(page, "operator", operator_password)
                page.get_by_role("button", name="生成并显示一次").click()
                expect(page.locator("#temporary-password")).to_be_visible()
                page.get_by_role("link", name="查看开通结果").click()
            expect(page.locator("main")).to_contain_text("处理完成", timeout=20000)
            print("SECURITY_STEP_OK " + kind, flush=True)
            if kind == "结束全部登录":
                rejected = httpx.get(
                    BASE + "/api/v2/me",
                    headers={"Authorization": "Bearer " + old_tokens["access_token"]},
                )
                assert rejected.status_code == 401
                refresh = httpx.post(
                    "http://127.0.0.1:8081/realms/office/protocol/openid-connect/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": PRODUCT["client_id"],
                        "refresh_token": old_tokens["refresh_token"],
                    },
                )
                assert refresh.status_code == 400
                print("OLD_ACCESS_AND_REFRESH_REJECTED", flush=True)
                worker.goto(BASE + "/console/dashboard")
                assert (
                    worker.locator("main h2").count() == 0
                    or worker.locator("main h2").inner_text() != "仪表盘"
                )
            if kind == "停用账号":
                worker.goto(BASE + "/console/dashboard")
                # BFF已失效或访问被停用，不能继续看到业务页面。
                assert (
                    worker.locator("main h2").count() == 0
                    or worker.locator("main h2").inner_text() != "仪表盘"
                )
        page.goto(operation_url)
        expect(page.locator("main")).to_contain_text("处理完成")
        for width in (1024, 1280, 1440, 1920):
            page.set_viewport_size({"width": width, "height": 1000})
            for path in (
                detail_url,
                BASE + "/console/accounts/new",
                BASE + "/console/accounts/operations",
            ):
                page.goto(path)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                    width,
                    path,
                )
        assert not errors, errors
        client.close()
        browser.close()
    print("ACCOUNT_LIFECYCLE_BROWSER_OK")


if __name__ == "__main__":
    main()
