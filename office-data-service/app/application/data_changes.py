"""数据管理预演与确认：回滚保存点复用实际业务规则，不产生预览记录或审计。"""

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable

from app.application.contracts import validate_output
from app.common.errors import require
from app.modules.data.service import Data, project_view


class DataChanges:
    def __init__(self, data: Data, audience: str):
        self.data, self.access, self.audience = data, data.access, audience

    async def execute(self, change):
        operation, payload = change["operation"], change["payload"]
        if operation == "project.create":
            return validate_output(
                operation, {"payload": payload}, await self.data.create_project(payload)
            )
        handlers: dict[str, Callable[..., Awaitable[dict]]] = {
            "project.update": self.data.update_project,
            "schema.publish": self.data.publish_schema,
            "record.update": self.data.update_record,
            "record.archive": self.data.archive_record,
        }
        return validate_output(operation, payload, await handlers[operation](**payload))

    async def preview(self, change):
        # 权限全局锁由Application持有；保存点只回滚数据预演，不能包含外部服务调用。
        operation, payload = change["operation"], change["payload"]
        actor = self.access.actor.id
        revision = self.access.revision.revision
        before = {}
        if operation.startswith(("project.", "schema.")):
            self.access.need("project.manage")
            if operation != "project.create":
                before = project_view(await self.data.project(payload["identifier"], lock=True))
                if operation == "schema.publish" and before["schema_revision"]:
                    before["schema"] = await self.data.schema(payload["identifier"])
        else:
            before = await self.data.get_record(payload["identifier"])
        replayed_before = self.access.db.info.get("idempotency_replayed", False)
        savepoint = await self.access.db.begin_nested()
        try:
            after = await self.execute(change)
            # 在回滚前取值，回滚后ORM对象可能过期，禁止隐式同步I/O。
            after = json.loads(json.dumps(after))
            await self.access.db.flush()
        finally:
            await savepoint.rollback()
            self.access.db.info["idempotency_replayed"] = replayed_before
        if operation == "project.create":
            after.pop("id", None)  # 预演UUID不是实际创建结果，不让客户端把它当成真实ID。
        digest = hashlib.sha256(
            json.dumps(
                {
                    "service": self.audience,
                    "actor": actor,
                    "access_revision": revision,
                    "change": change,
                    "before": before,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        return {
            "change": change,
            "confirmation_digest": digest,
            "before": before,
            "after": after,
            "notice": "预演不保存修改；请核对完整前后内容，人工确认后提交。摘要不是授权凭证。",
        }

    async def apply(self, change, confirmation_digest):
        preview = await self.preview(change)
        require(
            hmac.compare_digest(preview["confirmation_digest"], confirmation_digest),
            409,
            "version_conflict",
            "对象、权限或内容已变化，请重新预览和确认",
        )
        return {"operation": change["operation"], "result": await self.execute(change)}
