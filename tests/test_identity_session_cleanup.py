"""服务端身份会话归属校验；随Linux服务端质量门运行，不属于Windows客户端契约。"""

import asyncio

import httpx
import pytest
from app.common.errors import DomainError
from app.infrastructure.identity.keycloak import KeycloakAdmin


@pytest.mark.parametrize(
    "sessions,expected",
    [
        ([], 0),
        ([{"id": "target", "userId": "another"}], -1),
        ([{"id": "other", "userId": "employee"}], 0),
        ([{"id": "target", "userId": "employee"}], 1),
    ],
)
def test_native_cleanup_verifies_subject_ownership(tmp_path, sessions, expected):
    deleted = []
    secret = tmp_path / "secret"
    secret.write_text("isolated-fixture")
    secret.chmod(0o600)

    async def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "isolated", "expires_in": 60})
        assert "/admin/realms/office/" in request.url.path
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        assert request.url.path.endswith("/users/employee/sessions")
        return httpx.Response(200, json=sessions)

    async def scenario():
        client = KeycloakAdmin(
            "https://identity.invalid/realms/office",
            "managed",
            secret,
            transport=httpx.MockTransport(handler),
        )
        try:
            if expected == -1:
                with pytest.raises(DomainError) as caught:
                    await client.end_session("employee", "target")
                assert caught.value.status == 503
            else:
                await client.end_session("employee", "target")
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert len(deleted) == max(0, expected)
