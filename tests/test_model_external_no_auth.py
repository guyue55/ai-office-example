"""文件模式也应遵循无密钥语义，不读取宿主环境秘密。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.application.agent_service import AgentCoordinator
from app.infrastructure.config import Settings


def test_external_no_auth_starts_without_reading_key(tmp_path, monkeypatch):
    config = tmp_path / "agent.toml"
    config.write_text(
        'service_url="https://office.invalid"\nmodel_url="https://model.invalid/v1"\nmodel="fixture"\nauth_mode="none"\napproved_model_data=true\n'
    )
    settings = Settings(
        database_user="fixture",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://office.invalid/mcp",
        public_url="https://office.invalid",
        agent_config_file=config,
    )

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(get=AsyncMock(return_value=None))

    def forbidden(config):
        raise AssertionError("无密钥模式不得读取密钥引用")

    monkeypatch.setattr("app.application.agent_service.recover_runs", AsyncMock())
    monkeypatch.setattr("office_agent.providers.model_key", forbidden)

    async def scenario():
        coordinator = AgentCoordinator(SimpleNamespace(settings=settings, sessions=sessions))
        coordinator.tests.recover = AsyncMock()
        await coordinator.start()
        assert coordinator.runner is not None and coordinator.error == ""
        await coordinator.close()

    asyncio.run(scenario())
