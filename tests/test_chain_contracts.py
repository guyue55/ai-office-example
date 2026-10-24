"""跨输入、协议、业务边界的反例矩阵，不依赖真实凭证。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.entrypoints.mcp.validation import ValidatedMCPServer
from app.infrastructure.config import Settings
from app.main import create_app
from mcp.server.mcpserver.exceptions import ToolError


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("project.list", {"limit": True}),
        ("project.list", {"limit": "2"}),
        ("project.list", {"limit": 1.0}),
        ("project.list", {"limit": 101}),
        ("project.list", {"after": "x" * 101}),
        ("project.list", {"ignored": "private"}),
        ("schema.get", {"identifier": "p", "revision": 2**100}),
        ("record.archive", {"identifier": "r", "version": "1"}),
        ("record.list", {"project_id": ""}),
        ("project.list", {"after": "\x00"}),
        ("project.create", {"payload": {"name": "bad\x00name"}}),
    ],
)
def test_invalid_command_is_rejected_before_database(operation, payload):
    app = Application(None, None, None)

    async def run():
        with pytest.raises(DomainError) as caught:
            await app.call(None, operation, payload, "test-chain")
        assert caught.value.status == 422 and caught.value.details
        assert "private" not in str(caught.value)

    asyncio.run(run())


def test_mcp_raw_arguments_never_coerce_or_echo():
    server = ValidatedMCPServer("isolated")

    async def run():
        for args in ({"limit": True}, {"limit": "private-value"}, {"extra": "private-value"}):
            with pytest.raises(ToolError) as caught:
                await server.call_tool("list_projects", args)
            assert '"code":422001' in str(caught.value).replace(" ", "")
            assert "private-value" not in str(caught.value)

    asyncio.run(run())


@pytest.fixture
def app(monkeypatch, tmp_path):
    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    monkeypatch.setattr("app.main.async_sessions", lambda settings: SimpleNamespace())
    application = create_app(settings)
    application.state.application.verifier.verify_async = AsyncMock(return_value=object())
    application.state.application.call = AsyncMock(return_value={})
    return application


@pytest.mark.parametrize(
    "method,path,kwargs,status,code",
    [
        ("GET", "/unknown", {}, 404, 404001),
        ("POST", "/health/live", {}, 405, 405001),
        (
            "POST",
            "/api/v2/records",
            {"content": b"{}", "headers": {"Content-Type": "text/plain"}},
            415,
            415001,
        ),
        (
            "POST",
            "/api/v2/records",
            {
                "content": b'{"version":1,"version":2}',
                "headers": {"Content-Type": "application/json"},
            },
            400,
            400002,
        ),
        ("POST", "/api/v2/records", {"content": b"x" * 1048577}, 413, 413001),
        (
            "POST",
            "/api/v2/records",
            {"content": b"{", "headers": {"Content-Type": "application/json"}},
            400,
            400002,
        ),
        ("GET", "/api/v2/projects?limit=1&limit=2", {}, 422, 422008),
        ("GET", "/api/v2/projects?unknown=private-value", {}, 422, 422008),
        ("GET", "/health/live", {"headers": {"Host": "untrusted.invalid"}}, 400, 400001),
    ],
)
def test_http_error_matrix_has_consistent_envelope(app, method, path, kwargs, status, code):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer synthetic"},
        ) as client:
            response = await client.request(method, path, **kwargs)
            assert response.status_code == status
            body = response.json()
            assert body["code"] == code
            assert body["meta"]["trace_id"] == response.headers["X-Trace-ID"]
            assert "private-value" not in response.text
            if status == 405:
                assert "GET" in response.headers["Allow"]

    asyncio.run(run())
