"""真实本地链路夹具；仅构造账户与项目，不清空已有数据库。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from uuid import uuid4

import httpx
import pytest
from app.infrastructure.config import Settings
from app.infrastructure.database import sessions
from app.infrastructure.models import IdentityLink
from live_environment import SERVICE_URL
from office_client import OfficeClient
from playwright.sync_api import sync_playwright
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]


class MemoryStore:
    """只在测试进程内保存，不提供 CLI 或生产明文回退。"""

    def __init__(self):
        self.value = None

    def save(self, value):
        self.value = value

    def load(self):
        return self.value

    def clear(self):
        self.value = None


def browser_login(username, *, no_browser=False):
    urls = Queue()
    store = MemoryStore()
    client = OfficeClient(SERVICE_URL + "", store=store, browser_open=urls.put)
    with ThreadPoolExecutor(max_workers=1) as executor:
        if no_browser:
            client.oauth.browser_open = lambda _: pytest.fail("no-browser路径不得自动打开浏览器")
            future = executor.submit(client.login, 60, on_authorize=urls.put, open_browser=False)
        else:
            future = executor.submit(client.oauth.login, 60)
        url = urls.get(timeout=10)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                headless=True,
            )
            page = browser.new_page()
            page.goto(url)
            page.locator("#username").fill(username)
            filename = "operator_password" if username == "operator" else "employee_password"
            page.locator("#password").fill(
                (ROOT / "runtime/secrets" / filename).read_text().strip()
            )
            page.locator("#kc-login").click()
            page.wait_for_url("http://127.0.0.1:8765/callback**", timeout=15000)
            browser.close()
        future.result(timeout=10)
    return client


@pytest.fixture(scope="session")
def live():
    if os.environ.get("OFFICE_LIVE_TESTS") != "1":
        pytest.skip("需要显式 OFFICE_LIVE_TESTS=1 和本机独立开发环境")
    config = Settings.load(str(ROOT / "runtime/foundation/config.json"))
    if (
        config.database_host != "127.0.0.1"
        or config.database_name != "office"
        or config.database_port != 55432
    ):
        pytest.fail("仅允许独立本机测试库")
    assert config.public_url == SERVICE_URL, "验收入口与专用实例配置不一致"
    assert httpx.get(config.public_url + "/health/ready").status_code == 200
    operator, employee = browser_login("operator"), browser_login("employee")
    identities = json.loads((ROOT / "runtime/foundation/identities.json").read_text())
    with sessions(config)() as db:
        identifier = db.scalar(
            select(IdentityLink.employee_id).where(
                IdentityLink.issuer == config.issuer,
                IdentityLink.subject == identities["employee"]["subject"],
            )
        )
    if identifier is None:
        identifier = operator.request(
            "POST",
            "/api/v2/access/employees",
            json={
                "issuer": config.issuer,
                "subject": identities["employee"]["subject"],
                "name": "本地测试员工",
            },
        )["id"]
    bundle = operator.request(
        "POST", "/api/v2/access/permission-sets", json={"name": "测试权限-" + uuid4().hex}
    )
    role = operator.request(
        "POST", "/api/v2/access/roles", json={"name": "测试角色-" + uuid4().hex}
    )
    base_permissions = ["project.read", "record.create", "record.read.own", "record.manage.own"]
    bundle = operator.request(
        "PUT",
        f"/api/v2/access/permission-sets/{bundle['id']}/permissions",
        json={"version": bundle["version"], "permissions": base_permissions},
    )
    operator.request(
        "PUT",
        f"/api/v2/access/roles/{role['id']}/permission-sets",
        json={"version": role["version"], "ids": [bundle["id"]]},
    )
    current = operator.request("GET", f"/api/v2/access/employees/{identifier}/roles")
    operator.request(
        "PUT",
        f"/api/v2/access/employees/{identifier}/roles",
        json={"version": current["version"], "ids": [role["id"]]},
    )
    yield {
        "operator": operator,
        "employee": employee,
        "employee_id": identifier,
        "bundle": bundle,
        "permissions": base_permissions,
        "settings": config,
    }
    operator.close()
    employee.close()


@pytest.fixture
def project(live):
    operator = live["operator"]
    obj = operator.request(
        "POST", "/api/v2/projects", json={"name": "基础验证项目-" + uuid4().hex[:8]}
    )
    schema = operator.request(
        "POST",
        f"/api/v2/projects/{obj['id']}/schema",
        json={
            "expected_revision": 0,
            "fields": [
                {
                    "id": "amount",
                    "label": "测试金额",
                    "type": "decimal",
                    "required": True,
                    "description": "仅构造数据验证十进制存储",
                },
                {
                    "id": "note",
                    "label": "备注",
                    "type": "text",
                    "required": False,
                    "description": "测试说明",
                },
            ],
        },
    )
    return {**obj, "schema": schema}


def pytest_collection_modifyitems(items):
    """按真实夹具依赖登记活体验收，CI可显式排除而不伪装为完整验收。"""
    for item in items:
        if "live" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.live)
