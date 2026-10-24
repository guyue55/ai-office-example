"""修复后的真实PG与浏览器验证；仅用独立开发环境和专用测试身份。"""

import importlib.util
import os
import socket
import time
from pathlib import Path
from threading import Thread
from uuid import uuid4

import httpx
import pytest
import uvicorn
from app.main import create_app
from office_client import ServiceError
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def test_replay_is_denied_after_read_revocation(live, project):
    user, admin = live["employee"], live["operator"]
    payload = {
        "project_id": project["id"],
        "schema_revision": 1,
        "values": {"amount": "100"},
        "idempotency_key": uuid4().hex,
    }
    record = user.submit(**payload)
    admin.request(
        "PATCH",
        f"/api/v2/records/{record['id']}",
        json={"version": record["version"], "values": {"amount": "900"}},
    )
    path = f"/api/v2/access/permission-sets/{live['bundle']['id']}/permissions"
    grant = admin.request("GET", path)
    permissions = live["permissions"]
    changed = admin.request(
        "PUT",
        path,
        json={
            "version": grant["version"],
            "permissions": [p for p in permissions if p != "record.read.own"],
        },
    )
    try:
        with pytest.raises(ServiceError) as caught:
            user.submit(**payload)
        assert caught.value.code == 403001
    finally:
        admin.request("PUT", path, json={"version": changed["version"], "permissions": permissions})
    assert user.submit(**payload)["values"]["amount"] == "900"


def test_large_legal_records_page_through_without_loss(live):
    admin, user = live["operator"], live["employee"]
    project = admin.request("POST", "/api/v2/projects", json={"name": "字节分页-" + uuid4().hex})
    fields = [
        {
            "id": f"field_{i}",
            "label": "字段",
            "type": "text",
            "required": True,
            "description": "分页验收",
        }
        for i in range(100)
    ]
    admin.request(
        "POST",
        f"/api/v2/projects/{project['id']}/schema",
        json={"expected_revision": 0, "fields": fields},
    )
    ids = set()
    for _ in range(6):
        row = user.submit(
            project_id=project["id"],
            schema_revision=1,
            values={f"field_{i}": "x" * 4000 for i in range(100)},
            idempotency_key=uuid4().hex,
        )
        ids.add(row["id"])
    found = []
    after = ""
    for _ in range(10):
        page = user.records(project_id=project["id"], after=after)
        found.extend(row["id"] for row in page["items"])
        if page["next"] is None:
            break
        after = page["next"]
    assert set(found) == ids and len(found) == 6


@pytest.fixture
def web_listener():
    """由操作系统分配并预留端口，不能占用其他开发服务或先探测再抢占。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        yield listener


def test_fresh_web_client_provisions_and_logs_in(live, tmp_path, web_listener):
    port = web_listener.getsockname()[1]
    public_url = f"http://127.0.0.1:{port}"
    spec = importlib.util.spec_from_file_location(
        "isolated_provision", ROOT / "scripts/dev_stack.py"
    )
    stack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stack)
    client_id = "office-rebuild-" + uuid4().hex[:10]
    with httpx.Client(base_url="http://127.0.0.1:8081", timeout=15) as http:
        token = http.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "foundation-admin",
                "password": (ROOT / "runtime/secrets/admin_password").read_text().strip(),
            },
        )
        token.raise_for_status()
        http.headers["Authorization"] = "Bearer " + token.json()["access_token"]
        scopes = http.get("/admin/realms/office/client-scopes")
        scopes.raise_for_status()
        scope = next(row["id"] for row in scopes.json() if row["name"] == "office")
        for _ in range(2):
            stack.ensure_client(
                http,
                "/admin/realms/office",
                scope,
                client_id,
                public_url + "/auth/callback",
                live["settings"].audience,
            )
        response = http.get("/admin/realms/office/clients", params={"clientId": client_id})
        assert len(response.json()) == 1
    secret = tmp_path / "secret"
    secret.write_text("a" * 40)
    secret.chmod(0o600)
    settings = live["settings"].model_copy(
        update={
            "web_client_id": client_id,
            "public_url": public_url,
            "web_session_secret_file": secret,
        }
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=port, access_log=False, log_level="error"
        )
    )
    thread = Thread(target=server.run, kwargs={"sockets": [web_listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                headless=True,
            )
            page = browser.new_page()
            page.goto(public_url + "/console")
            page.locator("#username").fill("operator")
            page.locator("#password").fill(
                (ROOT / "runtime/secrets/operator_password").read_text().strip()
            )
            page.locator("#kc-login").click()
            expect(page.locator("main h2")).to_have_text("仪表盘")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
