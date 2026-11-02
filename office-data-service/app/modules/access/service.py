"""所有入口共用的实时授权；授权修改与审计在一个数据库事务内。"""

import asyncio
import hashlib
import json
import logging
import time
from uuid import uuid4

import casbin
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.common.errors import require
from app.common.security_context import actor_context, agent_run_context
from app.infrastructure.models import (
    AccessRevision,
    Audit,
    ClientAuthorization,
    Employee,
    EmployeeRole,
    IdentityLink,
    PendingIdentity,
    Permission,
    PermissionSet,
    Role,
    RoleSet,
    SetPermission,
)
from app.modules.access.catalog import PERMISSIONS
from app.modules.access.client_sessions import check_session

CASBIN_MODEL = """
[request_definition]
r = sub, act
[policy_definition]
p = sub, act
[role_definition]
g = _, _
[policy_effect]
e = some(where (p.eft == allow))
[matchers]
m = g(r.sub, p.sub) && r.act == p.act
"""


class Access:
    client_session: ClientAuthorization | None
    actor: Employee
    revision: AccessRevision
    enforcer: casbin.Enforcer

    def __init__(self, session, trace_id: str):
        self.db, self.trace_id = session, trace_id

    @classmethod
    async def create(cls, session, principal, trace_id: str, exclusive: bool = False):
        self = cls(session, trace_id)
        # 写锁互斥；普通操作共享锁。确保撤权提交后开始的操作读取新授权。
        lock_started = time.monotonic()
        self.revision = await session.scalar(
            select(AccessRevision).where(AccessRevision.id == 1).with_for_update(read=not exclusive)
        )
        lock_ms = round((time.monotonic() - lock_started) * 1000, 2)
        if lock_ms >= 250:
            logging.getLogger("office.service").warning(
                "[Trace: OfficeService/access/lock_wait] trace_id=%s exclusive=%s duration_ms=%s",
                trace_id,
                exclusive,
                lock_ms,
            )
        require(self.revision is not None, 503, "not_initialized", "基础权限尚未初始化")
        self.actor = await session.scalar(
            select(Employee)
            .join(IdentityLink)
            .where(
                IdentityLink.issuer == principal.issuer,
                IdentityLink.subject == principal.subject,
            )
        )
        require(
            self.actor is not None and self.actor.active and self.actor.security_hold is None,
            403,
            "inactive_employee",
            "员工未开通或已停用",
        )
        require(
            self.actor.tokens_after == 0
            or (
                type(principal.claims.get("iat")) is int
                and principal.claims["iat"] > self.actor.tokens_after
            ),
            401,
            "invalid_token",
            "登录凭证已撤销，请重新登录",
        )
        actor_context.set(self.actor.id)
        self.client_session = await check_session(self, principal)
        self.enforcer = await self._enforcer()
        return self

    @staticmethod
    async def trace_pending_identity(session, principal):
        """拒绝事务释放连接后登记；与认领共用权限锁，避免重建已绑定身份。"""
        await session.scalar(
            select(AccessRevision).where(AccessRevision.id == 1).with_for_update(read=True)
        )
        linked = await session.scalar(
            select(IdentityLink.id).where(
                IdentityLink.issuer == principal.issuer,
                IdentityLink.subject == principal.subject,
            )
        )
        if linked is not None:
            return
        display_name = str(
            principal.claims.get("name")
            or principal.claims.get("preferred_username")
            or principal.subject
        )[:200]
        # 原子 upsert 保留首次访问；并发首登无需捕获唯一约束异常。
        statement = insert(PendingIdentity).values(
            issuer=principal.issuer, subject=principal.subject, display_name=display_name
        )
        await session.execute(
            statement.on_conflict_do_update(
                constraint="uq_pending_identity_subject",
                set_={"display_name": display_name, "last_seen_at": func.now()},
            )
        )

    async def _enforcer(self):
        # 一次关联查询仅装载当前员工授权，避免每个请求扫描全公司权限。
        grants = (
            await self.db.execute(
                select(EmployeeRole.role_id, RoleSet.set_id, SetPermission.permission_code)
                .join(RoleSet, RoleSet.role_id == EmployeeRole.role_id)
                .join(SetPermission, SetPermission.set_id == RoleSet.set_id)
                .where(EmployeeRole.employee_id == self.actor.id)
            )
        ).all()

        actor_id = self.actor.id

        def build():
            model = casbin.Model()
            model.load_model_from_text(CASBIN_MODEL)
            enforcer = casbin.Enforcer(model, enable_log=False)
            for role_id, set_id, code in grants:
                enforcer.add_grouping_policy("u:" + actor_id, "r:" + role_id)
                enforcer.add_grouping_policy("r:" + role_id, "s:" + set_id)
                enforcer.add_policy("s:" + set_id, code)
            return enforcer

        return await asyncio.to_thread(build)

    def allows(self, code: str):
        return self.enforcer.enforce("u:" + self.actor.id, code)

    async def effective_permissions(self):
        """当前员工真实有效权限；身份展示与助手历史边界共用。"""
        return sorted(
            code for code in await self.db.scalars(select(Permission.code)) if self.allows(code)
        )

    async def authorization_fingerprint(self):
        """仅本人的有效权限及安全纪元；他人授权变化不造成无关会话中断。"""
        value = {
            "actor": self.actor.id,
            "permissions": await self.effective_permissions(),
            "tokens_after": self.actor.tokens_after,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def need(self, code: str):
        require(self.allows(code), 403, "permission_denied", "缺少操作权限：" + code)

    def audit(self, action: str, resource: str, detail: dict | None = None):
        detail = dict(detail or {})
        if run_id := agent_run_context.get():
            detail["agent_run_id"] = run_id
        self.db.add(
            Audit(
                actor_id=self.actor.id,
                action=action,
                resource_id=resource,
                detail=detail,
                trace_id=self.trace_id,
            )
        )

    async def changed(self, resource: str, detail: dict):
        (await self.db.flush())
        # 与固定的正向权限模型等价；管理员保护不依赖任何角色名称。
        survivor = await self.db.scalar(
            select(Employee.id)
            .join(EmployeeRole)
            .join(RoleSet, RoleSet.role_id == EmployeeRole.role_id)
            .join(SetPermission, SetPermission.set_id == RoleSet.set_id)
            .where(
                Employee.active,
                Employee.security_hold.is_(None),
                SetPermission.permission_code.in_(["access.read", "access.manage"]),
            )
            .group_by(Employee.id)
            .having(func.count(func.distinct(SetPermission.permission_code)) == 2)
            .limit(1)
        )
        require(
            survivor is not None,
            409,
            "last_administrator",
            "至少保留一名同时具备授权查看和管理权限的在职员工",
        )
        self.revision.revision += 1
        self.audit("access.changed", resource, {**detail, "revision": self.revision.revision})


def bootstrap(session, issuer: str, subject: str, name: str):
    """离线一次性初始化，只允许在空员工表执行；不提供 HTTP 后门。"""
    session.scalar(select(AccessRevision).where(AccessRevision.id == 1).with_for_update())
    require(
        session.scalar(select(Employee.id).limit(1)) is None,
        409,
        "already_initialized",
        "已有员工，禁止重复引导",
    )
    for code, description in PERMISSIONS.items():
        session.merge(Permission(code=code, description=description))
    session.flush()
    user = Employee(id=str(uuid4()), name=name)
    role = Role(id=str(uuid4()), name="基础管理员")
    bundle = PermissionSet(id=str(uuid4()), name="基础管理权限集")
    session.add_all([user, role, bundle])
    session.flush()
    session.add_all(
        [
            IdentityLink(employee_id=user.id, issuer=issuer, subject=subject),
            EmployeeRole(employee_id=user.id, role_id=role.id),
            RoleSet(role_id=role.id, set_id=bundle.id),
            *[SetPermission(set_id=bundle.id, permission_code=code) for code in PERMISSIONS],
            Audit(
                actor_id=user.id,
                action="access.bootstrap",
                resource_id=user.id,
                detail={"reason": "离线首管理员初始化"},
                trace_id=str(uuid4()),
            ),
        ]
    )
    return user.id
