"""慢身份校验不得阻塞事件循环、积压无限线程或因请求取消绕过容量。"""

import asyncio
from threading import Event

import pytest
from app.common.errors import DomainError
from app.infrastructure.config import Settings
from app.modules.access.authentication import Principal, TokenVerifier


def test_auth_capacity_survives_request_cancellation(tmp_path):
    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
        auth_workers=1,
    )
    verifier = TokenVerifier(settings)
    entered, release = Event(), Event()

    def slow_verify(token):
        entered.set()
        assert release.wait(3)
        return Principal(settings.issuer, "employee", {})

    verifier.verify = slow_verify

    async def exercise():
        first = asyncio.create_task(verifier.verify_async("test-only"))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            # 能执行到这里也证明慢同步校验未占住事件循环。
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(DomainError) as caught:
                await verifier.verify_async("must-not-start")
            assert caught.value.code == "authentication_busy"
        finally:
            release.set()
        async with asyncio.timeout(2):
            while verifier._capacity.locked():
                await asyncio.sleep(0.001)
        assert (await verifier.verify_async("next")).subject == "employee"

    asyncio.run(exercise())


def test_auth_failure_releases_capacity(tmp_path):
    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
        auth_workers=1,
    )
    verifier = TokenVerifier(settings)

    def rejected(token):
        raise DomainError(401, "invalid_token", "凭证无效")

    verifier.verify = rejected

    async def exercise():
        for _ in range(2):
            with pytest.raises(DomainError) as caught:
                await verifier.verify_async("test-only")
            assert caught.value.status == 401

    asyncio.run(exercise())
