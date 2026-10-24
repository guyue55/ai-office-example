"""AI 客户端管理变更：只读预览与事务内重新核对，复用既有管理实现。"""

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from app.common.errors import require
from app.infrastructure.models import PendingIdentity, Permission, PermissionSet, Role
from app.modules.access.catalog import describe_permission
from app.modules.access.inspection import Inspection
from app.modules.access.management import Management
from app.modules.access.provisioning import default_role
from app.modules.access.schemas import normalize_access_command


class Changes:
    def __init__(self, management: Management, audience: str):
        self.management = management
        self.access, self.audience = management.access, audience

    async def preview(self, change: dict):
        self.access.need("access.read")
        operation = change["operation"]
        if operation == "access.employee.update" and not self.access.allows("access.manage"):
            self.access.need("employee.update")
        else:
            self.access.need("access.manage")
        payload = normalize_access_command(operation, change["payload"])
        before, after, detail = {}, {}, None
        added, removed = [], []
        if operation == "access.identity.claim":
            pending = await self.access.db.get(PendingIdentity, payload["pending_id"])
            require(
                pending is not None and pending.claimed_by is None,
                409,
                "identity_claimed",
                "待认领登录不存在或已处理",
            )
            require(
                pending.issuer == self.management.issuer,
                422,
                "issuer_mismatch",
                "只能认领本实例信任的身份来源",
            )
            before = {"pending_id": pending.id, "display_name": pending.display_name}
            after = dict(payload)
            if payload["assign_default_role"]:
                role = await default_role(self.access.db)
                role_detail = await Inspection(self.access).detail("roles", role.id)
                after["assigned_role"] = {"id": role.id, "name": role.name}
                after["permissions"] = role_detail["permissions"]
        elif operation == "access.create":
            if payload["kind"] == "employees":
                require(
                    payload["body"]["issuer"] == self.management.issuer,
                    422,
                    "issuer_mismatch",
                    "只能绑定本实例信任的身份来源",
                )
            after = dict(payload["body"])
            if payload["kind"] == "employees" and payload["body"]["assign_default_role"]:
                role = await default_role(self.access.db)
                detail_role = await Inspection(self.access).detail("roles", role.id)
                added = [
                    {"id": role.id, "name": role.name, "description": "首次开通关联的默认角色"}
                ]
                after["assigned_role"] = added[0]
                after["permissions"] = detail_role["permissions"]
        else:
            kind = "employees" if operation == "access.employee.update" else payload["kind"]
            detail = await Inspection(self.access).detail(kind, payload["identifier"])
            body = payload["body"]
            if operation == "access.employee.update" and body["active"] != detail["active"]:
                self.access.need("access.manage")
            require(
                detail["version"] == body["version"],
                409,
                "version_conflict",
                "配置已更新，请重新预览",
            )
            if operation == "access.employee.update":
                before = {"name": detail["name"], "active": detail["active"]}
                after = {"name": body["name"], "active": body["active"]}
            else:
                ids = body.get("permissions", body.get("ids", []))
                require(len(ids) == len(set(ids)), 422, "duplicate_links", "关联项不能重复")
                relation = payload["relation"]
                model: Any = {
                    "roles": Role,
                    "permission-sets": PermissionSet,
                    "permissions": Permission,
                }[relation]
                key = model.code if relation == "permissions" else model.id
                rows = (await self.access.db.scalars(select(model).where(key.in_(ids)))).all()
                require(len(rows) == len(ids), 422, "unknown_link", "关联项未登记")
                refs = [
                    {
                        "id": row.code,
                        "name": describe_permission(row.code)["name"],
                        "description": row.description,
                    }
                    if relation == "permissions"
                    else {"id": row.id, "name": row.name, "description": ""}
                    for row in rows
                ]
                refs.sort(key=lambda row: row["id"])
                old_ids = {row["id"] for row in detail["links"]}
                new_ids = set(ids)
                added = [row for row in refs if row["id"] not in old_ids]
                removed = [row for row in detail["links"] if row["id"] not in new_ids]
                before, after = {"links": detail["links"]}, {"links": refs}
        normalized = {"operation": operation, "payload": payload}
        binding = {
            "actor": self.access.actor.id,
            "service": self.audience,
            "access_revision": self.access.revision.revision,
            "change": normalized,
            "before": before,
        }
        digest = hashlib.sha256(
            json.dumps(binding, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return {
            "change": normalized,
            "confirmation_digest": digest,
            "target": {"id": detail["id"], "name": detail["name"], "description": ""}
            if detail
            else None,
            "before": before,
            "after": after,
            "added": added,
            "removed": removed,
            "used_by": detail["used_by"]
            if detail
            else {"items": [], "has_more": False, "next": None},
            "access_revision": self.access.revision.revision,
            "notice": "完整替换关联会撤销未列入的旧关联。请展示变更与使用者，等待用户确认；摘要不是授权凭证。使用者分页未完时继续查询详情。",
        }

    async def apply(self, change: dict, confirmation_digest: str):
        preview = await self.preview(change)
        require(
            hmac.compare_digest(preview["confirmation_digest"], confirmation_digest),
            409,
            "version_conflict",
            "变更内容、操作人或权限已变化，请重新预览和确认",
        )
        operation, payload = preview["change"]["operation"], preview["change"]["payload"]
        handlers: dict[str, Callable[..., Awaitable[dict]]] = {
            "access.identity.claim": self.management.claim_identity,
            "access.create": self.management.create,
            "access.employee.update": self.management.update_employee,
            "access.links.replace": self.management.replace,
        }
        result = await handlers[operation](**payload)
        return {"operation": operation, **result, "ids": result.get("ids")}
