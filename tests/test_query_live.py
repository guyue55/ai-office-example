"""已启动服务 + 真实Keycloak/PG/MCP + 已安装CLI；不注入测试令牌或替换认证。"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import test_lifecycle_live
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from office_client import ServiceError
from office_client.auth import KeyringStore
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.live
# 复用并注册最小权限临时确认夹具，结束后恢复原角色。
query_confirmer = test_lifecycle_live.confirmer


@pytest.fixture
def live_query(live, query_confirmer):
    admin, user = query_confirmer, live["employee"]
    project = admin.request(
        "POST", "/api/v2/projects", json={"name": "聚合活体验收-" + uuid4().hex[:8]}
    )
    admin.request(
        "POST",
        f"/api/v2/projects/{project['id']}/schema",
        json={
            "expected_revision": 0,
            "fields": [
                {"id": key, "label": key, "description": "构造技术字段", "type": kind}
                for key, kind in (("v", "decimal"), ("d", "date"), ("label", "text"))
            ],
        },
    )
    records = []
    for actor, values in [
        (user, {"v": "0.10", "d": "2025-12-31", "label": "a"}),
        (user, {"v": "0.20", "d": "2026-01-01", "label": "a"}),
        (user, {"d": "2026-01-02", "label": "b"}),
        (admin, {"v": "99.00", "d": "2026-01-01", "label": "z"}),
    ]:
        record = actor.submit(
            project_id=project["id"], schema_revision=1, values=values, idempotency_key=uuid4().hex
        )
        admin.confirm(record["id"], record["version"])
        records.append(record)
    query = {"measures": [{"alias": "n", "op": "count"}, {"alias": "s", "op": "sum", "field": "v"}]}
    proof = {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "project_id": project["id"],
        "checks": [],
        "real_identity": True,
    }
    folder = ROOT / "runtime/foundation/query-activation"
    folder.mkdir(mode=0o700, exist_ok=True)
    previous = folder / "live-result.json"
    if previous.exists():
        prior = json.loads(previous.read_text())
        if prior.get("commit") == proof["commit"]:
            proof["checks"] = prior["checks"]
    yield {
        **live,
        "project": project,
        "records": records,
        "query": query,
        "proof": proof,
        "folder": folder,
    }
    (folder / "live-result.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2))


def test_active_http_sdk_permissions_and_real_mcp(live_query):
    ctx = live_query
    user, admin = ctx["employee"], ctx["operator"]
    identifier, query = ctx["project"]["id"], ctx["query"]
    cap = user.request("GET", "/api/v2/capabilities")
    assert cap["aggregation"] and cap["transport_coverage"]["data.aggregate"]["cli"] == "aggregate"
    assert user.aggregate(identifier, 1, query)["rows"] == [{"n": 3, "s": "0.30"}]
    assert admin.aggregate(identifier, 1, query, scope="all")["rows"] == [{"n": 4, "s": "99.30"}]
    with pytest.raises(ServiceError) as caught:
        user.aggregate(identifier, 1, query, scope="all")
    assert caught.value.status == 403
    grouped = {
        "group_by": [{"field": "d", "bucket": "year"}, {"field": "label"}],
        "measures": [{"alias": "n", "op": "count"}],
    }
    assert user.aggregate(identifier, 1, grouped)["rows"] == [
        {"d": "2025-01-01", "label": "a", "n": 1},
        {"d": "2026-01-01", "label": "a", "n": 1},
        {"d": "2026-01-01", "label": "b", "n": 1},
    ]
    with pytest.raises(ServiceError) as caught:
        user.aggregate(identifier, 1, {**grouped, "max_groups": 1})
    assert caught.value.code == 422017
    response = httpx.post(
        ctx["settings"].public_url + "/api/v2/data/aggregate",
        headers={"Authorization": "Bearer " + user.oauth.access_token()},
        json={
            "identifier": identifier,
            "revision": 1,
            "query": {"measures": [{"alias": "n", "op": "sum", "field": "unknown"}]},
        },
    )
    assert response.status_code == 422 and response.json()["code"] == 422005
    assert response.json()["meta"]["trace_id"] == response.headers["x-trace-id"]

    async def mcp():
        async with (
            httpx.AsyncClient(
                headers={"Authorization": "Bearer " + user.oauth.access_token()}
            ) as http,
            streamable_http_client(ctx["settings"].public_url + "/mcp", http_client=http) as (
                read,
                write,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert "aggregate_data" in {t.name for t in tools}
            result = await session.call_tool(
                "aggregate_data", {"identifier": identifier, "revision": 1, "query": query}
            )
            assert not result.is_error
            assert json.loads(result.content[0].text)["rows"] == [{"n": 3, "s": "0.30"}]
            denied = await session.call_tool(
                "aggregate_data",
                {"identifier": identifier, "revision": 1, "scope": "all", "query": query},
            )
            assert denied.is_error and "403001" in denied.content[0].text

    asyncio.run(mcp())
    ctx["proof"]["checks"].append("active_http_sdk_network_mcp_scopes_errors_exact_numbers")


def test_installed_cli_real_browser_keychain_and_aggregate(live_query):
    ctx = live_query
    # uv run会把开发环境bin置前；不能把editable入口当成已安装候选CLI。
    cli = (
        Path(subprocess.check_output(["uv", "tool", "dir", "--bin"], text=True).strip()) / "office"
    )
    if sys.platform != "darwin" or not cli.is_file():
        pytest.skip("原生钥匙串与已安装CLI验收仅适用已准备的本机macOS环境")
    # security不带-w，仅检查条目元信息；从未占用的别名开始，不读取旧凭证。
    for host in ("localhost", "LocalHost", "LOCALHOST", "localHost"):
        url = f"http://{host}:8800"
        service = "office:" + url
        found = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", "oauth"],
            capture_output=True,
            check=False,
        )
        if found.returncode == 44:
            break
    else:
        pytest.fail("验收别名均有既有钥匙串记录，拒绝覆盖")
    cli = str(cli)
    store = KeyringStore(url)
    process = None
    try:
        process = subprocess.Popen(
            [cli, "--service", url, "login", "--no-browser", "--timeout", "90"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        pending = json.loads(process.stdout.readline())
        assert pending["event"] == "authorization_pending"
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                headless=True,
            )
            page = browser.new_page()
            page.set_default_timeout(15000)
            page.goto(pending["authorization_url"])
            page.locator("#username").fill("employee")
            page.locator("#password").fill(
                (ROOT / "runtime/secrets/employee_password").read_text().strip()
            )
            page.locator("#kc-login").click()
            page.wait_for_url(ctx["settings"].redirect_uri + "**")
            browser.close()
        stdout, _ = process.communicate(timeout=20)
        assert process.returncode == 0, "已安装CLI真实登录失败，未回显授权输出"
        assert json.loads(stdout)["id"] == ctx["employee_id"]
        path = ctx["folder"] / "query.json"
        path.write_text(json.dumps(ctx["query"]))
        result = subprocess.run(
            [
                cli,
                "--service",
                url,
                "aggregate",
                ctx["project"]["id"],
                "--revision",
                "1",
                "--file",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, "已安装CLI聚合失败，未回显内部输出"
        assert json.loads(result.stdout)["rows"] == [{"n": 3, "s": "0.30"}]
        logout = subprocess.run(
            [cli, "--service", url, "logout"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert logout.returncode == 0 and store.load() is None
        ctx["proof"]["checks"].append("installed_cli_browser_pkce_native_keychain_aggregate_logout")
        tool = (
            Path(subprocess.check_output(["uv", "tool", "dir"], text=True).strip())
            / "office-client/lib"
        )
        installed = next(tool.glob("python*/site-packages/office_client/client.py"))
        assert (
            hashlib.sha256(installed.read_bytes()).digest()
            == hashlib.sha256(
                (ROOT / "clients/python/src/office_client/client.py").read_bytes()
            ).digest()
        )
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        # 只清理本次确认不存在后创建的条目，不涉及原服务地址的登录。
        store.clear()
