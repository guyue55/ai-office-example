"""临时PG真实dump/restore；模型及身份为合成数据，不访问现有实例。"""

import asyncio
import subprocess
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytest.importorskip("office_agent", reason="需可选Agent依赖")
from app.application.agent_service import AgentCoordinator
from app.application.service import Application
from app.infrastructure.database import async_sessions

pytestmark = pytest.mark.live


def test_model_database_and_root_key_restore(isolated_query_db, tmp_path):  # noqa: F811
    settings = isolated_query_db
    name = (settings.database_password_file.parent / "container-name").read_text()
    assert (
        name.startswith("office-query-test-") and name.removeprefix("office-query-test-").isalnum()
    )
    root = tmp_path / "source-key"
    root.write_bytes(Fernet.generate_key())
    root.chmod(0o600)
    configured = settings.model_copy(
        update={
            "agent_secret_key_file": root,
            "agent_model_urls": ["https://model.invalid/v1"],
            "agent_model_url_policy": "allowlist",
        }
    )

    def command(*args, data=None):
        result = subprocess.run(
            ["docker", "exec", "-i", name, *args],
            input=data,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, "隔离备份恢复操作失败，不回显数据库内容"
        return result.stdout

    async def seed():
        factory, _, _, admin, *_ = await dataset(settings)
        app = Application(configured, factory, None)
        app._agent = AgentCoordinator(app)

        async def call(op, body):
            return await app.call(admin, op, body, uuid4().hex)

        try:
            state = await call(
                "agent.config.save",
                {
                    "version": 0,
                    "draft": {
                        "model_url": "https://model.invalid/v1",
                        "model": "synthetic",
                        "approved_model_data": True,
                    },
                    "secret_action": "replace",
                    "secret": "synthetic-key",
                },
            )
            state = await call(
                "agent.config.apply",
                {"version": state["version"], "revision": state["draft_revision"]},
            )
            active = state["active_revision"]
            state = await call(
                "agent.config.save",
                {
                    "version": state["version"],
                    "draft": {**state["draft"], "model": "unapplied-draft"},
                    "secret_action": "keep",
                },
            )
            return admin, active, state["draft_revision"]
        finally:
            await app._agent.close()
            await factory.kw["bind"].dispose()

    admin, active, draft = asyncio.run(seed())
    backup = command("pg_dump", "-Fc", "-U", "postgres", "-d", "office")
    assert backup.startswith(b"PGDMP")
    restored = "office_restore_" + uuid4().hex
    command("createdb", "-U", "postgres", "-O", "office_owner", "-T", "template0", restored)
    command("pg_restore", "--exit-on-error", "-U", "postgres", "-d", restored, data=backup)
    copied = tmp_path / "restored-key"
    copied.write_bytes(root.read_bytes())
    copied.chmod(0o600)
    wrong = tmp_path / "wrong-key"
    wrong.write_bytes(Fernet.generate_key())
    wrong.chmod(0o600)

    async def verify(key, ready):
        config = configured.model_copy(
            update={"database_name": restored, "agent_secret_key_file": key}
        )
        factory = async_sessions(config)
        app = Application(config, factory, None)
        app._agent = AgentCoordinator(app)
        try:
            status = await app.call(admin, "agent.config.get", {}, uuid4().hex)
            assert status["ready"] is ready
            assert status["active_revision"] == active and status["draft_revision"] == draft
            assert status["draft"]["model"] == "unapplied-draft"
            # 密钥错误只降级Agent，既有权限及普通数据查询仍有效。
            page = await app.call(admin, "project.list", {"limit": 10}, uuid4().hex)
            assert page["items"]
        finally:
            await app._agent.close()
            await factory.kw["bind"].dispose()

    for key, ready in [(copied, True), (tmp_path / "missing-key", False), (wrong, False)]:
        asyncio.run(verify(key, ready))
