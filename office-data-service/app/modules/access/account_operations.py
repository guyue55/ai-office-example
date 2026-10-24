"""账号命令的本地校验与持久化；不执行身份HTTP请求。"""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.common.errors import require
from app.infrastructure.models import (
    Employee,
    IdentityLink,
    IdentityOperation,
    Role,
    RoleSet,
    SetPermission,
)
from app.modules.access.provisioning import default_role

ACTIVE_STATES = ("pending", "running", "awaiting_user", "needs_attention", "needs_confirmation")
PERMISSION = {
    "create": "identity.create",
    "link": "identity.read",
    "update": "identity.update",
    "disable": "identity.update",
    "enable": "identity.update",
    "reset": "identity.credentials.manage",
    "logout": "identity.sessions.revoke",
}


def fingerprint(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def account_info(account, linked_employee_id=None):
    data = asdict(account)
    data.pop("actions")
    data.pop("operation_id")
    return {**data, "linked_employee_id": linked_employee_id}


def operation_view(op):
    return {
        "id": op.id,
        "version": op.version,
        "employee_name": op.request.get("employee_name", op.request["command"].get("name", "")),
        "kind": op.kind,
        "status": op.status,
        "step": op.step,
        "message": op.message,
        "employee_id": op.employee_id,
        "username": op.request["command"].get("username", ""),
        "next_url": f"/console/accounts/operations/{op.id}",
        "created_at": op.created_at,
        "updated_at": op.updated_at,
    }


class AccountOperations:
    def __init__(self, access, issuer, email_enabled):
        self.access, self.db, self.issuer, self.email_enabled = (
            access,
            access.db,
            issuer,
            email_enabled,
        )

    def need(self, kind):
        self.access.need("access.read")
        self.access.need("identity.read")
        self.access.need(PERMISSION[kind])
        if kind in ("create", "link", "disable", "enable"):
            self.access.need("access.manage")

    async def target(self, identifier):
        obj = await self.db.get(Employee, identifier)
        require(obj is not None, 404, "not_found", "业务人员不存在")
        link = await self.db.scalar(
            select(IdentityLink).where(
                IdentityLink.employee_id == identifier, IdentityLink.issuer == self.issuer
            )
        )
        require(link is not None, 409, "constraint_conflict", "此人员没有可管理的登录账号")
        return obj, link

    async def roles(self, ids):
        if ids is None:
            ids = [(await default_role(self.db)).id]
        require(len(ids) == len(set(ids)), 422, "duplicate_links", "角色不能重复")
        found = (await self.db.scalars(select(Role).where(Role.id.in_(ids)))).all()
        require(len(found) == len(ids), 422, "unknown_link", "角色未登记，请重新选择")
        grants = (
            await self.db.execute(
                select(RoleSet.role_id, SetPermission.permission_code)
                .join(SetPermission, SetPermission.set_id == RoleSet.set_id)
                .where(RoleSet.role_id.in_(ids))
                .order_by(RoleSet.role_id, SetPermission.permission_code)
            )
        ).all()
        snapshot = fingerprint(
            {
                "roles": sorted((r.id, r.version) for r in found),
                "permissions": list(map(list, grants)),
            }
        )
        return sorted(ids), snapshot, [r.name for r in found]

    async def preview(self, command):
        command = dict(command)
        kind = command["kind"]
        self.need(kind)
        effects = []
        roles_snapshot = ""
        if kind in ("create", "link"):
            command["role_ids"], roles_snapshot, names = await self.roles(command.get("role_ids"))
            effects.append("关联角色：" + ("、".join(names) if names else "暂不授予业务角色"))
        if kind == "create":
            effects.insert(0, "创建登录账号并绑定业务人员：" + command["name"])
        elif kind == "link":
            exists = await self.db.scalar(
                select(IdentityLink.id).where(
                    IdentityLink.issuer == self.issuer, IdentityLink.subject == command["remote_id"]
                )
            )
            require(
                exists is None, 409, "constraint_conflict", "该账号已绑定人员，请直接查看现有人员"
            )
            effects.insert(0, "关联核实后的现有登录账号，不重置其密码")
        else:
            obj, _link = await self.target(command["identifier"])
            require(
                obj.version == command["version"],
                409,
                "version_conflict",
                "人员信息已更新，请重新核对",
            )
            if kind in ("disable", "reset", "logout"):
                require(
                    obj.id != self.access.actor.id,
                    409,
                    "constraint_conflict",
                    "本人请使用账号与安全入口；请另一位管理员处理管理性安全操作",
                )
            effects.append(
                {
                    "disable": "立即禁止业务访问，再处理登录停用",
                    "enable": "先核实登录账号，再恢复当前保留的业务角色",
                    "reset": "重置凭证并撤销旧访问；处理未完成时临时禁止访问",
                    "logout": "结束已有登录；处理期间临时禁止访问",
                    "update": "修改指定资料，不改动其他字段",
                }[kind]
                + "："
                + obj.name
            )
        if command.get("mode") == "email":
            require(
                self.email_enabled, 422, "invalid_request", "邮件功能尚未配置，请使用临时密码方式"
            )
            if kind == "create":
                require(
                    bool(command.get("email")), 422, "invalid_input", "邮件设置密码需要工作邮箱"
                )
        binding = {
            "actor": self.access.actor.id,
            "revision": self.access.revision.revision,
            "command": command,
            "roles_snapshot": roles_snapshot,
        }
        return {
            "request": command,
            "confirmation_digest": fingerprint(binding),
            "access_revision": self.access.revision.revision,
            "effects": effects,
        }, roles_snapshot

    async def submit(self, command, digest, key, principal):
        raw_fingerprint = fingerprint(command)
        old = await self.db.scalar(
            select(IdentityOperation).where(
                IdentityOperation.actor_id == self.access.actor.id,
                IdentityOperation.idempotency_key == key,
            )
        )
        if old:
            self.need(old.kind)
            require(
                old.fingerprint == raw_fingerprint,
                409,
                "constraint_conflict",
                "相同提交编号对应不同内容，请重新核对",
            )
            self.db.info["idempotency_replayed"] = True
            return old
        preview, snapshot = await self.preview(command)
        require(
            preview["confirmation_digest"] == digest,
            409,
            "version_conflict",
            "人员、角色或权限已变化，请重新核对确认",
        )
        normalized = preview["request"]
        obj = None
        link = None
        if "identifier" in normalized:
            obj, link = await self.target(normalized["identifier"])
            pending = await self.db.scalar(
                select(IdentityOperation.id).where(
                    IdentityOperation.employee_id == obj.id,
                    IdentityOperation.status.in_(ACTIVE_STATES),
                )
            )
            require(
                pending is None,
                409,
                "constraint_conflict",
                "该人员已有未完成操作，请先在账号详情处理",
            )
        op = IdentityOperation(
            id=str(uuid4()),
            actor_id=self.access.actor.id,
            employee_id=obj.id if obj else None,
            remote_id=link.subject if link else None,
            kind=normalized["kind"],
            status="pending",
            step="start",
            request={
                "employee_name": obj.name if obj else normalized.get("name", ""),
                "command": normalized,
                "roles_snapshot": snapshot,
                "executor_id": self.access.actor.id,
                "actor_iat": principal.claims.get("iat", 0),
                "actor_session": {
                    key: principal.claims[key]
                    for key in ("sid", "azp", "exp", "iat")
                    if key in principal.claims
                },
            },
            fingerprint=raw_fingerprint,
            idempotency_key=key,
            message="已受理，正在处理",
            trace_id=self.access.trace_id,
            access_revision=self.access.revision.revision,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            expected_version=obj.version if obj else None,
        )
        if obj and op.kind in ("disable", "reset", "logout"):
            if op.kind == "disable":
                obj.active = False
            else:
                require(
                    obj.security_hold is None, 409, "constraint_conflict", "人员正在处理安全操作"
                )
                obj.security_hold = op.id
            obj.tokens_after = int(datetime.now(UTC).timestamp())
            obj.version += 1
            op.expected_version = obj.version
            await self.access.changed(
                obj.id, {"kind": "employees", "operation": "update", "reason": "账号安全操作已受理"}
            )
        self.db.add(op)
        self.access.audit(
            "account.submitted", op.id, {"kind": op.kind, "employee_id": op.employee_id}
        )
        await self.db.flush()
        return op
