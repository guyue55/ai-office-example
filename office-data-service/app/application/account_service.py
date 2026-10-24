"""账号管理请求编排，持久化提交与外部只读查询分离。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select

from app.application.account_runner import AccountRunner
from app.common.errors import DomainError, require
from app.common.pagination import bounded_page
from app.infrastructure.identity.keycloak import KeycloakAdmin
from app.infrastructure.models import EmployeeRole, IdentityLink, IdentityOperation, Role
from app.infrastructure.pagination import numbered_rows
from app.modules.access.account_operations import (
    AccountOperations,
    account_info,
    fingerprint,
    operation_view,
)
from app.modules.access.service import Access


class Accounts:
    def __init__(self, settings, sessions):
        self.settings, self.sessions = settings, sessions
        config = settings.identity_management
        self.provider = (
            KeycloakAdmin(settings.issuer, config.client_id, config.client_secret_file)
            if config
            else None
        )
        self.runner = AccountRunner(settings, sessions, self.provider)

    def enabled(self):
        require(
            self.provider is not None,
            503,
            "identity_unavailable",
            "账号管理连接尚未配置，请联系系统管理员",
        )

    async def context(self, db, principal, trace, exclusive=False):
        access = await Access.create(db, principal, trace, exclusive)
        domain = AccountOperations(
            access,
            self.settings.issuer,
            bool(
                self.settings.identity_management
                and self.settings.identity_management.email_enabled
            ),
        )
        return access, domain

    async def request(self, principal, operation, payload, trace, finish):
        if operation == "account.capabilities":
            async with self.sessions.begin() as db:
                await self.context(db, principal, trace)
                return finish(
                    {
                        "enabled": self.provider is not None,
                        "email_enabled": bool(
                            self.settings.identity_management
                            and self.settings.identity_management.email_enabled
                        ),
                    }
                )
        self.enabled()
        if operation in ("account.search", "account.detail"):
            return await self.read_external(principal, operation, payload, trace, finish)
        async with self.sessions.begin() as db:
            access, domain = await self.context(
                db, principal, trace, operation in {"account.submit", "account.resume"}
            )
            access.need("identity.read")
            access.need("access.read")
            if operation == "account.history":
                query = select(IdentityOperation)
                if payload.get("identifier"):
                    query = query.where(IdentityOperation.employee_id == payload["identifier"])
                if payload.get("q"):
                    query = query.where(
                        or_(
                            IdentityOperation.request["employee_name"].astext.contains(
                                payload["q"], autoescape=True
                            ),
                            IdentityOperation.request["command"]["username"].astext.contains(
                                payload["q"], autoescape=True
                            ),
                        )
                    )
                for field in ("status", "kind"):
                    if payload.get(field):
                        query = query.where(getattr(IdentityOperation, field) == payload[field])
                if payload.get("since"):
                    query = query.where(
                        IdentityOperation.created_at >= datetime.fromisoformat(payload["since"])
                    )
                if payload.get("until"):
                    query = query.where(
                        IdentityOperation.created_at < datetime.fromisoformat(payload["until"])
                    )
                if payload.get("page") is not None:
                    rows, pagination = await numbered_rows(
                        db,
                        query.order_by(
                            IdentityOperation.created_at.desc(), IdentityOperation.id.desc()
                        ),
                        payload["page"],
                        payload["limit"],
                    )
                    return finish(
                        {
                            "items": [operation_view(row) for row in rows],
                            "next": None,
                            "has_more": pagination["page"] < pagination["pages"],
                            "pagination": pagination,
                        }
                    )
                if payload["after"]:
                    anchor = await db.get(IdentityOperation, payload["after"])
                    require(
                        anchor is not None
                        and (
                            not payload.get("identifier")
                            or anchor.employee_id == payload["identifier"]
                        ),
                        422,
                        "invalid_pagination",
                        "分页位置无效，请返回首页",
                    )
                    query = query.where(
                        or_(
                            IdentityOperation.created_at < anchor.created_at,
                            and_(
                                IdentityOperation.created_at == anchor.created_at,
                                IdentityOperation.id < anchor.id,
                            ),
                        )
                    )
                rows = (
                    await db.scalars(
                        query.order_by(
                            IdentityOperation.created_at.desc(), IdentityOperation.id.desc()
                        ).limit(payload["limit"] + 1)
                    )
                ).all()
                result = bounded_page(rows, operation_view, lambda row: row.id, payload["limit"])
                return finish({**result, "has_more": result["next"] is not None})
            if operation == "account.preview":
                preview, _ = await domain.preview(payload["request"])
                return finish(preview)
            if operation == "account.submit":
                op = await domain.submit(
                    payload["request"],
                    payload["confirmation_digest"],
                    payload["idempotency_key"],
                    principal,
                )
                result = finish(
                    operation_view(op), replayed=db.info.get("idempotency_replayed", False)
                )
                self.runner.wake.set()
                return result
            if operation == "account.operation":
                op = await db.get(IdentityOperation, payload["identifier"])
                require(op is not None, 404, "not_found", "处理记录不存在")
                return finish(operation_view(op))
            if operation in ("account.resume_preview", "account.resume"):
                op = await db.get(
                    IdentityOperation,
                    payload["identifier"],
                    with_for_update=operation == "account.resume",
                )
                require(op is not None, 404, "not_found", "处理记录不存在")
                require(
                    op.status in ("needs_attention", "needs_confirmation", "awaiting_user"),
                    409,
                    "version_conflict",
                    "此操作正在处理或已经完成",
                )
                command = dict(op.request["command"])
                target_version = op.expected_version
                employee = None
                if op.employee_id:
                    employee, _ = await domain.target(op.employee_id)
                    target_version = employee.version
                if command.get("identifier"):
                    command["version"] = target_version
                if payload.get("mode"):
                    require(
                        op.kind in ("create", "reset"),
                        422,
                        "invalid_request",
                        "此操作不能修改凭证方式",
                    )
                    command["mode"] = payload["mode"]
                preview, snapshot = await domain.preview(command)
                # 续办摘要同时绑定原操作和当前人员版本，开户绑定后也可恢复并发变更。
                preview["confirmation_digest"] = fingerprint(
                    {
                        "base": preview["confirmation_digest"],
                        "operation": op.id,
                        "operation_version": op.version,
                        "target_version": target_version,
                    }
                )
                if employee is not None and op.kind in ("create", "link"):
                    names = (
                        await db.scalars(
                            select(Role.name)
                            .join(EmployeeRole, EmployeeRole.role_id == Role.id)
                            .where(EmployeeRole.employee_id == employee.id)
                            .order_by(Role.name)
                        )
                    ).all()
                    preview["effects"] = [
                        "继续已绑定的账号，不重复开户或重新分配角色",
                        "人员：" + employee.name,
                        "保留当前角色：" + ("、".join(names) if names else "未配置角色"),
                    ]
                if operation == "account.resume_preview":
                    return finish(preview)
                require(
                    payload["version"] == op.version
                    and payload["confirmation_digest"] == preview["confirmation_digest"],
                    409,
                    "version_conflict",
                    "处理记录已变化，请重新核对确认",
                )
                op.request = {
                    **op.request,
                    "command": preview["request"],
                    "roles_snapshot": snapshot,
                    "executor_id": access.actor.id,
                    "actor_iat": principal.claims.get("iat", 0),
                    "actor_session": {
                        key: principal.claims[key]
                        for key in ("sid", "azp", "exp", "iat")
                        if key in principal.claims
                    },
                }
                op.expected_version = target_version
                op.expires_at = datetime.now(UTC) + timedelta(minutes=15)
                op.version += 1
                op.updated_at = datetime.now(UTC)
                op.lease_owner = None
                op.lease_until = None
                if op.step in ("credential", "credential_uncertain", "mail_uncertain", "mail"):
                    op.step = "credential" if command.get("mode") == "temporary" else "mail"
                    op.status = "awaiting_user" if op.step == "credential" else "pending"
                else:
                    op.status = "pending"
                op.message = "已重新确认，继续处理"
                access.audit(
                    "account.submitted", op.id, {"kind": op.kind, "reason": "管理员重新核对并继续"}
                )
                await db.flush()
                result = finish(operation_view(op))
                self.runner.wake.set()
                return result
        raise DomainError(422, "unsupported_operation", "尚未提供此账号能力")

    async def read_external(self, principal, operation, payload, trace, finish):
        async with self.sessions.begin() as db:
            access, domain = await self.context(db, principal, trace)
            access.need("identity.read")
            access.need("access.read")
            if operation == "account.detail":
                employee, link = await domain.target(payload["identifier"])
                remote = link.subject
        assert self.provider is not None
        if operation == "account.search":
            found = await self.provider.search(payload["q"])
            async with self.sessions.begin() as db:
                access, _ = await self.context(db, principal, trace)
                access.need("identity.read")
                mapping = dict(
                    (
                        await db.execute(
                            select(IdentityLink.subject, IdentityLink.employee_id).where(
                                IdentityLink.issuer == self.settings.issuer,
                                IdentityLink.subject.in_([a.id for a in found]),
                            )
                        )
                    ).all()
                )
                return finish(
                    {
                        "items": [
                            account_info(account, mapping.get(account.id)) for account in found
                        ],
                        "notice": "最多显示20项，请细化关键词核对唯一账号",
                    }
                )
        account = None
        notice = ""
        try:
            account = await self.provider.get(remote)
        except DomainError:
            notice = "登录资料暂时无法核实；业务状态仍按本地规则限制，请稍后刷新"
        async with self.sessions.begin() as db:
            access, domain = await self.context(db, principal, trace)
            access.need("identity.read")
            employee, _ = await domain.target(payload["identifier"])
            return finish(
                {
                    "employee_id": employee.id,
                    "version": employee.version,
                    "name": employee.name,
                    "active": employee.active,
                    "security_pending": employee.security_hold is not None,
                    "account": account_info(account, employee.id) if account else None,
                    "available": account is not None,
                    "notice": notice,
                }
            )
