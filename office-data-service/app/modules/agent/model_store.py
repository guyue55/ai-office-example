"""模型配置持久化与权限；不在此模块调用供应商或控制运行器。"""

import asyncio

from app.common.errors import require
from app.infrastructure import model_secrets
from app.infrastructure.models import ModelConfiguration, ModelConfigurationRevision
from app.modules.agent.model_configuration import ModelSave


class ModelStore:
    def __init__(self, access, settings):
        self.access, self.db, self.settings = access, access.db, settings

    async def state(self):
        state = await self.db.get(ModelConfiguration, 1, with_for_update=True)
        require(state is not None, 503, "not_initialized", "请先执行模型配置数据库迁移")
        return state

    async def revision(self, revision):
        row = await self.db.get(ModelConfigurationRevision, revision)
        require(row is not None, 404, "not_found", "模型配置修订不存在")
        return row

    @staticmethod
    def expect(state, version):
        require(state.version == version, 409, "version_conflict", "配置已被修改，请刷新后重新核对")

    async def save(self, state, payload):
        self.access.need("agent.config.manage")
        self.access.need("agent.config.read")
        command = ModelSave.model_validate(payload)
        self.expect(state, command.version)
        require(
            command.draft.model_url in self.settings.agent_model_urls,
            422,
            "invalid_input",
            "模型地址不在运维允许列表中，请先联系运维配置出口",
        )
        # 即使保留／清除，也验证根密钥，避免产生无法继续管理的托管配置。
        await asyncio.to_thread(model_secrets.cipher, self.settings.agent_secret_key_file)
        previous = await self.revision(state.draft_revision) if state.draft_revision else None
        secret = previous.secret_ciphertext if previous else None
        if command.secret_action == "replace":
            assert command.secret is not None
            secret = await asyncio.to_thread(
                model_secrets.encrypt,
                self.settings.agent_secret_key_file,
                command.secret.get_secret_value(),
            )
        elif command.secret_action == "clear":
            secret = None
        # 元数据永远不含secret、api_key_file或宿主环境引用。
        state.version += 1
        state.draft_revision = state.version
        self.db.add(
            ModelConfigurationRevision(
                revision=state.version,
                config=command.draft.model_dump(),
                secret_ciphertext=secret,
                actor_id=self.access.actor.id,
            )
        )
        self.access.audit(
            "agent.config.saved",
            str(state.version),
            {"revision": state.version, "secret_action": command.secret_action},
        )
        await self.db.flush()
        return state

    async def status(self, state, *, installed, ready, message):
        self.access.need("agent.config.read")
        row = await self.revision(state.draft_revision) if state.draft_revision else None
        active = (
            await self.revision(state.active_revision)
            if state.source == "managed" and state.active_revision
            else None
        )
        try:
            await asyncio.to_thread(model_secrets.cipher, self.settings.agent_secret_key_file)
            secret_store_ready = True
        except Exception:  # noqa: BLE001 -- 只返回状态，不暴露路径或底层异常。
            secret_store_ready = False
        return {
            "version": state.version,
            "draft_revision": state.draft_revision,
            "active_revision": state.active_revision,
            "source": state.source,
            "installed": installed,
            "ready": ready,
            "secret_store_ready": secret_store_ready,
            "secret_set": bool(row and row.secret_ciphertext),
            "draft": row.config if row else None,
            "active": active.config if active else None,
            "allowed_urls": list(self.settings.agent_model_urls),
            "message": message,
        }
