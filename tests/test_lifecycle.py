"""验证应用生命周期在异常和取消退出时仍释放数据库连接池。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.infrastructure.config import Settings
from app.main import create_app


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_lifespan_releases_pool_on_abnormal_exit(monkeypatch, tmp_path, failure):
    disposed = AsyncMock()
    factory = SimpleNamespace(kw={"bind": SimpleNamespace(dispose=disposed)})

    @asynccontextmanager
    async def manager():
        yield

    monkeypatch.setattr("app.main.async_sessions", lambda settings: factory)
    monkeypatch.setattr(
        "app.main.build_mcp",
        lambda application: (
            SimpleNamespace(session_manager=SimpleNamespace(run=manager)),
            lambda: None,
        ),
    )
    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    app = create_app(settings)

    async def exercise():
        with pytest.raises(failure):
            async with app.router.lifespan_context(app):
                raise failure()
        disposed.assert_awaited_once()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field,value",
    [
        ("pool_size", 0),
        ("max_overflow", -1),
        ("pool_timeout", 0),
        ("statement_timeout_ms", "1;SELECT 1"),
    ],
)
def test_database_runtime_rejects_unsafe_limits(field, value):
    from app.infrastructure.config import DatabaseRuntime

    with pytest.raises(ValueError):
        DatabaseRuntime.model_validate({field: value})


def test_database_runtime_defaults_preserve_existing_budget():
    from app.infrastructure.config import DatabaseRuntime

    limits = DatabaseRuntime()
    assert (limits.pool_size, limits.max_overflow, limits.pool_timeout) == (5, 5, 3)
