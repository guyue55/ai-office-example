"""取消不释放仍在读磁盘的名额；账号管理读取不能阻塞事件循环。"""

import asyncio
from threading import Event

import httpx
import pytest
from app.common.async_work import WorkCapacityExceeded
from app.infrastructure.identity.keycloak import KeycloakAdmin
from app.infrastructure.secrets import read_secret


def test_secret_capacity_stays_reserved_until_thread_finishes(tmp_path, monkeypatch):
    entered, release, finished = Event(), Event(), Event()
    calls = []

    def slow(path):
        calls.append(path)
        entered.set()
        assert release.wait(3)
        finished.set()
        return "isolated-secret"

    monkeypatch.setattr("app.infrastructure.secrets._read_secret", slow)

    async def exercise():
        capacity = asyncio.Semaphore(1)
        first = asyncio.create_task(read_secret(tmp_path / "unused", capacity))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            # 多次重试也不得新增磁盘读取线程。
            for _ in range(5):
                with pytest.raises(WorkCapacityExceeded):
                    await read_secret(tmp_path / "unused", capacity)
            assert len(calls) == 1 and not finished.is_set()
        finally:
            release.set()
        async with asyncio.timeout(2):
            while capacity.locked():
                await asyncio.sleep(0.001)
        assert await read_secret(tmp_path / "unused", capacity) == "isolated-secret"

    asyncio.run(exercise())


def test_identity_secret_read_is_nonblocking_and_cancellation_safe(tmp_path, monkeypatch):
    entered, release = Event(), Event()

    def slow(path):
        entered.set()
        assert release.wait(3)
        return "isolated-secret"

    monkeypatch.setattr("app.infrastructure.secrets._read_secret", slow)

    async def exercise():
        client = KeycloakAdmin(
            "https://identity.invalid/realms/office",
            "checker",
            tmp_path / "unused",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "access_token": "isolated-admin-token",
                        "expires_in": 60,
                    },
                )
            ),
        )
        first = asyncio.create_task(client._access_token())
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            from app.common.errors import DomainError

            with pytest.raises(DomainError) as busy:
                await client._access_token()
            assert busy.value.code == "authentication_busy"
        finally:
            release.set()
            async with asyncio.timeout(2):
                while client._secret_capacity.locked():
                    await asyncio.sleep(0.001)
            await client.aclose()

    asyncio.run(exercise())


def test_online_verifier_cannot_bypass_secret_budget_by_cancelling(tmp_path, monkeypatch):
    from app.common.errors import DomainError
    from app.infrastructure.config import Settings, TokenIntrospection
    from app.modules.access.authentication import Principal, TokenVerifier

    entered, release = Event(), Event()
    calls = []

    def slow(path):
        calls.append(path)
        entered.set()
        assert release.wait(3)
        return "isolated-secret"

    monkeypatch.setattr("app.infrastructure.secrets._read_secret", slow)
    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
        auth_workers=1,
        token_introspection=TokenIntrospection(
            client_id="checker", client_secret_file=tmp_path / "unused"
        ),
    )
    verifier = TokenVerifier(settings)
    verifier.verify = lambda token: Principal(settings.issuer, "isolated", {})

    async def exercise():
        await verifier.http.aclose()
        verifier.http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"active": True}),
            )
        )
        first = asyncio.create_task(verifier.verify_async("isolated-first"))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            for _ in range(3):
                with pytest.raises(DomainError) as busy:
                    await verifier.verify_async("isolated-next")
                assert busy.value.code == "authentication_busy"
            assert len(calls) == 1
        finally:
            release.set()
            async with asyncio.timeout(2):
                while verifier._secret_capacity.locked():
                    await asyncio.sleep(0.001)
            await verifier.aclose()

    asyncio.run(exercise())
