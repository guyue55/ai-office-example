"""真实HTTP/MCP与SDK共享账号流程；返回进度而非明文凭证。"""

import asyncio
import json
import time
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from office_client import ServiceError


def test_account_http_contract_idempotency_and_forbidden_employee(live):
    client = live["operator"]
    command = {"kind": "create", "name": "HTTP开户验证", "username": "http-" + uuid4().hex[:12]}
    preview = client.preview_account(command)
    key = uuid4().hex
    token = client.oauth.access_token()
    response = httpx.post(
        live["settings"].public_url + "/api/v2/account-operations",
        headers={"Authorization": "Bearer " + token},
        json={
            "request": preview["request"],
            "confirmation_digest": preview["confirmation_digest"],
            "idempotency_key": key,
        },
    )
    assert response.status_code == 202
    envelope = response.json()
    assert set(envelope) == {"code", "message", "data", "meta"}
    assert isinstance(envelope["code"], int)
    original = envelope["data"]
    replay = httpx.post(
        live["settings"].public_url + "/api/v2/account-operations",
        headers={"Authorization": "Bearer " + token},
        json={
            "request": preview["request"],
            "confirmation_digest": preview["confirmation_digest"],
            "idempotency_key": key,
        },
    )
    assert replay.status_code == 202 and replay.json()["meta"]["replayed"] is True

    assert (
        client.submit_account(preview["request"], preview["confirmation_digest"], key)["id"]
        == original["id"]
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        operation = client.account_operation(original["id"])
        if operation["status"] not in ("pending", "running"):
            break
        time.sleep(0.1)
    assert operation["status"] == "awaiting_user", operation["message"]
    assert operation["next_url"].startswith("/console/accounts/operations/")
    assert "password" not in operation and "secret" not in operation
    matches = client.search_accounts(command["username"])
    assert len(matches["items"]) == 1
    person = client.employee_account(operation["employee_id"])
    assert not person["active"] and not person["account"]["enabled"]
    assert any(
        row["id"] == original["id"]
        for row in client.account_history(identifier=person["employee_id"])["items"]
    )
    with pytest.raises(ServiceError) as caught:
        live["employee"].employee_account(person["employee_id"])
    assert caught.value.status == 403


def test_account_mcp_real_routes_validate_and_share_permissions(live):
    async def scenario():
        for actor in ("operator", "employee"):
            token = live[actor].oauth.access_token()
            async with (
                httpx.AsyncClient(headers={"Authorization": "Bearer " + token}) as http,
                streamable_http_client(live["settings"].public_url + "/mcp", http_client=http) as (
                    read,
                    write,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {
                    "account_capabilities",
                    "search_login_accounts",
                    "preview_account_operation",
                    "submit_account_operation",
                    "account_operation_history",
                } <= names
                assert all("credential" not in name for name in names)
                result = await session.call_tool("account_capabilities", {})
                assert not result.is_error and json.loads(result.content[0].text)["enabled"]
                result = await session.call_tool("search_login_accounts", {"q": "operator"})
                assert result.is_error == (actor == "employee")
                if actor == "operator":
                    assert json.loads(result.content[0].text)["items"]
                invalid = await session.call_tool(
                    "preview_account_operation",
                    {
                        "request": {
                            "kind": "create",
                            "name": "账号",
                            "username": "candidate",
                            "password": "private-sentinel",
                        }
                    },
                )
                assert invalid.is_error and "private-sentinel" not in invalid.content[0].text

    asyncio.run(scenario())
