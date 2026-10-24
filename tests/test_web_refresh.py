"""真实会话存储＋异步刷新边界；外部身份响应隔离，敏感值仅测试内存。"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.common.errors import DomainError
from app.entrypoints.web.authentication import WebAuthentication, bind_identity
from app.entrypoints.web.session import LoginRequired, SessionStore
from office_client.product import PRODUCT
from test_web_console import web as console_fixture

web = console_fixture


def setup(monkeypatch, *, status=200, payload=None, subject="employee", sid="device-a"):
    store = SessionStore(b"isolated-test-key", 3600)
    principal = SimpleNamespace(
        issuer="https://identity.invalid",
        subject=subject,
        claims={"exp": int(time.time()) + 300, "sid": sid},
    )
    application = SimpleNamespace(
        settings=SimpleNamespace(issuer=principal.issuer, web_client_id="web", auth_workers=2),
        verifier=SimpleNamespace(verify_async=AsyncMock(return_value=principal)),
    )
    token = {
        "access_token": "old",
        "refresh_token": "old-refresh",
        "csrf": "csrf",
        "recent_auth": 1,
    }
    bind_identity(
        token,
        SimpleNamespace(
            issuer=principal.issuer, subject="employee", claims={"exp": 1, "sid": "device-a"}
        ),
    )
    cookie = store.create(token)
    count = []

    async def post(client, url, **kwargs):
        count.append(kwargs["data"])
        await asyncio.sleep(0.01)
        return httpx.Response(
            status,
            json=payload
            or {"access_token": "new", "refresh_token": "new-refresh", "token_type": "Bearer"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return SimpleNamespace(
        auth=WebAuthentication(application, store),
        store=store,
        token=token,
        cookie=cookie,
        calls=count,
        application=application,
    )


def test_concurrent_rotation_once_and_ttl_preserved(monkeypatch):
    x = setup(monkeypatch)
    deadline = next(iter(x.store.sessions.values()))[1]

    async def run():
        results = await asyncio.gather(*(x.auth.principal(x.cookie) for _ in range(8)))
        assert len(results) == 8

    asyncio.run(run())
    assert len(x.calls) == 1
    assert x.token["refresh_token"] == "new-refresh"
    assert x.token["csrf"] == "csrf" and x.token["recent_auth"] == 1
    assert next(iter(x.store.sessions.values()))[1] == deadline


@pytest.mark.parametrize("status", [400, 401, 500, 302])
def test_failure_does_not_affect_other_device(monkeypatch, status):
    x = setup(monkeypatch, status=status)
    other = x.store.create({"access_token": "other"})
    with pytest.raises(DomainError) as error:
        asyncio.run(x.auth.principal(x.cookie))
    assert error.value.status == (401 if status in (400, 401) else 503)
    assert len(x.calls) == 1
    with pytest.raises(LoginRequired):
        x.store.load(x.cookie)
    assert x.store.load(other)["access_token"] == "other"


@pytest.mark.parametrize("subject,sid", [("other", "device-a"), ("employee", "device-b")])
def test_rotated_identity_cannot_change(monkeypatch, subject, sid):
    x = setup(monkeypatch, subject=subject, sid=sid)
    with pytest.raises(DomainError) as error:
        asyncio.run(x.auth.principal(x.cookie))
    assert error.value.code == "invalid_token"


def test_no_refresh_when_not_due_or_missing_credential(monkeypatch):
    x = setup(monkeypatch)
    x.token["_expires_at"] = time.time() + 100
    asyncio.run(x.auth.principal(x.cookie))
    x.token["_expires_at"] = 1
    x.token.pop("refresh_token")
    asyncio.run(x.auth.principal(x.cookie))
    assert not x.calls


def test_logout_during_refresh_cannot_restore_session(monkeypatch):
    x = setup(monkeypatch)
    original = x.application.verifier.verify_async

    async def verify(token):
        x.store.destroy(x.cookie)
        return await original(token)

    x.application.verifier.verify_async = verify
    with pytest.raises(LoginRequired):
        asyncio.run(x.auth.principal(x.cookie))
    assert not x.store.sessions


def test_cancelled_exchange_is_not_retried(monkeypatch):
    x = setup(monkeypatch)

    async def run():
        task = asyncio.create_task(x.auth.principal(x.cookie))
        while not x.calls:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(LoginRequired):
            await x.auth.principal(x.cookie)

    asyncio.run(run())
    assert len(x.calls) == 1


def test_capacity_rejection_preserves_session(monkeypatch):
    x = setup(monkeypatch)

    async def run():
        await x.auth.capacity.acquire()
        await x.auth.capacity.acquire()
        try:
            with pytest.raises(DomainError) as error:
                await x.auth.principal(x.cookie)
            assert error.value.code == "authentication_busy"
            assert x.store.load(x.cookie) is x.token
            assert not x.calls
        finally:
            x.auth.capacity.release()
            x.auth.capacity.release()

    asyncio.run(run())


def test_lost_response_does_not_reuse_refresh_token(monkeypatch):
    x = setup(monkeypatch)
    calls = []

    async def lost(client, url, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("isolated-private-refresh-detail")

    monkeypatch.setattr(httpx.AsyncClient, "post", lost)
    with pytest.raises(DomainError) as error:
        asyncio.run(x.auth.principal(x.cookie))
    assert error.value.status == 503
    assert "isolated-private" not in error.value.message
    with pytest.raises(LoginRequired):
        asyncio.run(x.auth.principal(x.cookie))
    assert len(calls) == 1


def test_original_ttl_expiring_during_exchange_is_not_extended(monkeypatch):
    x = setup(monkeypatch)
    original = x.application.verifier.verify_async

    async def verify(token):
        key = x.cookie.split(".")[0]
        x.store.sessions[key] = (x.token, 0)
        return await original(token)

    x.application.verifier.verify_async = verify
    with pytest.raises(LoginRequired):
        asyncio.run(x.auth.principal(x.cookie))
    assert not x.store.sessions


def test_live_route_uses_refresh_and_keeps_csrf(web, monkeypatch):
    from app.entrypoints.web.authentication import bind_identity

    original = SimpleNamespace(
        issuer="https://identity.invalid",
        subject="employee",
        claims={"exp": 1, "sid": "web-device"},
    )
    refreshed = SimpleNamespace(
        issuer=original.issuer,
        subject=original.subject,
        claims={"exp": int(time.time()) + 300, "sid": "web-device"},
    )
    token = web.store.load(web.cookie)
    token["refresh_token"] = "isolated-refresh"
    bind_identity(token, original)
    web.application.verifier.verify_async = AsyncMock(return_value=refreshed)
    calls = []

    async def post(client, url, **kwargs):
        calls.append(kwargs["data"])
        return httpx.Response(
            200,
            json={"token_type": "Bearer", "access_token": "isolated-new"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    response = web.client.post(
        "/console/roles/new", data={"csrf": "form-csrf", "name": "续期后角色"}
    )
    assert response.status_code == 303
    assert len(calls) == 1 and calls[0]["client_id"] == PRODUCT["web_client_id"]
    assert token["csrf"] == "form-csrf"


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "new", "token_type": "Bearer", "refresh_token": None},
        {"access_token": "new", "token_type": "Basic"},
        {"token_type": "Bearer"},
        ["not-an-object"],
    ],
)
def test_malformed_token_response_is_closed_safely(monkeypatch, payload):
    x = setup(monkeypatch, payload=payload)
    with pytest.raises(DomainError) as error:
        asyncio.run(x.auth.principal(x.cookie))
    assert error.value.status == 503
    with pytest.raises(LoginRequired):
        x.store.load(x.cookie)
