"""客户端管理参数的封闭集合和自然语言工具发现，不依赖真实身份。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.application.commands import validate_command
from app.entrypoints.mcp.server import build_mcp
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "access.delete", "payload": {}},
        {
            "operation": "access.employee.update",
            "payload": {
                "identifier": "x",
                "body": {"name": "员工", "active": "false", "version": 1},
            },
        },
        {
            "operation": "access.links.replace",
            "payload": {
                "kind": "roles",
                "identifier": "r",
                "relation": "permissions",
                "body": {"permissions": [], "version": True},
            },
        },
        {
            "operation": "access.create",
            "payload": {"kind": "roles", "body": {"name": "新角色", "ignored": "private-value"}},
        },
    ],
)
def test_management_change_schema_rejects_undefined_or_coerced_inputs(change):
    with pytest.raises(ValidationError):
        validate_command("access.change.preview", {"change": change})


def test_management_tools_are_discoverable_and_strict():
    app = SimpleNamespace(
        settings=SimpleNamespace(
            issuer="https://identity.invalid", audience="https://service.invalid/mcp"
        ),
        verifier=SimpleNamespace(),
        call=AsyncMock(),
    )
    server, _ = build_mcp(app)

    async def run():
        tools = {t.name: t for t in await server.list_tools()}
        for name in (
            "list_access",
            "access_detail",
            "access_links",
            "preview_access_change",
            "apply_access_change",
        ):
            assert name in tools and tools[name].output_schema
            assert tools[name].input_schema["additionalProperties"] is False
        assert tools["preview_access_change"].annotations.read_only_hint is True
        assert tools["apply_access_change"].annotations.destructive_hint is True
        for name, args in (
            ("list_access", {"kind": "employees", "limit": "2"}),
            ("apply_access_change", {"change": {}, "confirmation_digest": "private-value"}),
        ):
            with pytest.raises(ToolError) as caught:
                await server.call_tool(name, args)
            assert "private-value" not in str(caught.value)
        app.call.assert_not_awaited()

    asyncio.run(run())


def test_read_only_preview_timeout_does_not_claim_unknown_write():
    import httpx
    from office_client import OfficeClient, ServiceError

    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-value", request=request)

    with OfficeClient("http://127.0.0.1:8800", store=SimpleNamespace()) as client:
        client.oauth.access_token = lambda: "synthetic"
        client.http.close()
        client.http = httpx.Client(base_url=client.url, transport=httpx.MockTransport(handle))
        with pytest.raises(ServiceError) as caught:
            client.preview_access_change({})
        assert not caught.value.outcome_unknown
        assert caught.value.recovery == "retry_read"
        assert len(calls) == 1
