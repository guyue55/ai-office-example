"""身份适配器只与HTTP边界交互；覆盖缓存、取消及无副作用重试。"""

import asyncio
from pathlib import Path

import httpx
import pytest
from app.common.errors import DomainError
from app.infrastructure.identity.keycloak import KeycloakAdmin


def run_case(tmp_path, handler, run):
    secret = tmp_path / "secret"
    secret.write_text("isolated-secret-not-for-real-auth")
    secret.chmod(0o600)

    async def scenario():
        client = KeycloakAdmin(
            "https://identity.invalid/realms/enterprise",
            "managed-client",
            secret,
            transport=httpx.MockTransport(handler),
        )
        try:
            await run(client)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_concurrent_calls_share_cached_token(tmp_path):
    token_calls = []

    async def handler(request):
        if request.url.path.endswith("/token"):
            token_calls.append(True)
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"access_token": "isolated", "expires_in": 60})
        assert request.headers["authorization"] == "Bearer isolated"
        return httpx.Response(
            200,
            json={
                "id": "a",
                "username": "a",
                "enabled": False,
                "attributes": {"officeOperation": ["op"]},
            },
        )

    async def scenario(client):
        accounts = await asyncio.gather(*(client.get("a") for _ in range(8)))
        assert len(token_calls) == 1
        assert all(account.operation_id == "op" for account in accounts)

    run_case(tmp_path, handler, scenario)


@pytest.mark.parametrize(
    "status,expected",
    [(409, 409), (403, 503), (401, 503), (400, 422), (429, 503), (503, 503), (302, 503)],
)
def test_errors_are_sanitized_without_retries(tmp_path, status, expected):
    attempts = []

    async def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "isolated", "expires_in": 60})
        attempts.append(request)
        return httpx.Response(status, json={"error": "private-upstream-body"})

    async def scenario(client):
        with pytest.raises(DomainError) as caught:
            await client.create("new-user", "新员工", "", "op")
        assert caught.value.status == expected
        assert "private-upstream-body" not in str(caught.value)
        assert len(attempts) == 1

    run_case(tmp_path, handler, scenario)


def test_create_requires_ownership_marker(tmp_path):
    async def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "isolated", "expires_in": 60})
        if request.method == "POST":
            return httpx.Response(201, headers={"Location": "https://untrusted.invalid/user"})
        account = {"id": "u", "username": "new-user", "enabled": False}
        return httpx.Response(
            200, json=[account] if request.url.path.endswith("/users") else account
        )

    async def scenario(client):
        with pytest.raises(DomainError) as caught:
            await client.create("new-user", "新员工", "", "op")
        assert caught.value.status == 503

    run_case(tmp_path, handler, scenario)


def test_write_timeout_is_not_retried(tmp_path):
    attempts = []

    async def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "isolated", "expires_in": 60})
        attempts.append(True)
        raise httpx.ReadTimeout("private-upstream-body", request=request)

    async def scenario(client):
        with pytest.raises(DomainError):
            await client.temporary_password("u", "must-not-be-in-error")
        assert len(attempts) == 1

    run_case(tmp_path, handler, scenario)


def test_master_realm_rejected(tmp_path):
    with pytest.raises(ValueError):
        KeycloakAdmin("https://identity.invalid/realms/master", "admin", Path("unused"))
