"""账号操作有界执行器：短事务租约，外部HTTP不占用数据库连接。"""

import asyncio
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import and_, or_, select, true

from app.common.errors import DomainError, require
from app.infrastructure.models import (
    AccessRevision,
    Audit,
    Employee,
    EmployeeRole,
    IdentityLink,
    IdentityOperation,
)
from app.modules.access.account_operations import AccountOperations
from app.modules.access.service import Access

logger = logging.getLogger("office.accounts")


class AccountRunner:
    def __init__(self, settings, sessions, provider):
        self.settings, self.sessions, self.provider = settings, sessions, provider
        self.owner = str(uuid4())
        self.wake = asyncio.Event()
        self.task = None

    async def start(self):
        if self.provider is not None:
            self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.provider is not None:
            await self.provider.aclose()

    async def loop(self):
        while True:
            try:
                identifier = await self.claim()
                if identifier:
                    await self.step(identifier)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 — 循环隔离，只记录类型，不打印账号/秘密
                logger.warning("账号执行器暂缓 error_type=%s", type(error).__name__)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass

    async def claim(self, identifier: str | None = None):
        now = datetime.now(UTC)
        async with self.sessions.begin() as db:
            row = await db.scalar(
                select(IdentityOperation)
                .where(
                    IdentityOperation.id == identifier if identifier else true(),
                    or_(
                        IdentityOperation.status.in_(["pending", "running"]),
                        and_(
                            IdentityOperation.status == "awaiting_user",
                            IdentityOperation.expires_at < now,
                        ),
                    ),
                    or_(
                        IdentityOperation.lease_until.is_(None), IdentityOperation.lease_until < now
                    ),
                )
                .order_by(IdentityOperation.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            if row.expires_at < now:
                row.status = "needs_confirmation"
                row.message = "确认已过期，请管理员重新核对后继续"
                db.add(
                    Audit(
                        actor_id=row.request["executor_id"],
                        action="account.attention",
                        resource_id=row.id,
                        detail={"kind": row.kind, "status": row.status, "reason": "原确认超时"},
                        trace_id=row.trace_id,
                    )
                )
                return None
            row.lease_owner = self.owner
            row.lease_until = now + timedelta(seconds=60)
            row.status = "running"
            row.version += 1
            row.updated_at = now
            return row.id

    async def authorized(self, db, op):
        link = await db.scalar(
            select(IdentityLink).where(
                IdentityLink.employee_id == op.request["executor_id"],
                IdentityLink.issuer == self.settings.issuer,
            )
        )
        require(link is not None, 403, "permission_denied", "原处理人员已不可用，请有权管理员接手")
        principal = SimpleNamespace(
            issuer=self.settings.issuer,
            subject=link.subject,
            claims={"iat": op.request.get("actor_iat", 0), **op.request.get("actor_session", {})},
        )
        access = await Access.create(db, principal, op.trace_id, True)
        domain = AccountOperations(
            access, self.settings.issuer, self.settings.identity_management.email_enabled
        )
        domain.need(op.kind)
        if op.kind in ("create", "link"):
            _, snapshot, _ = await domain.roles(op.request["command"]["role_ids"])
            require(
                snapshot == op.request["roles_snapshot"],
                409,
                "version_conflict",
                "角色能力已变化，请重新核对",
            )
        return access, domain

    async def local(self, identifier, callback, *, release=True):
        async with self.sessions.begin() as db:
            # 与请求侧保持相同锁序：授权修订在前，操作行在后，避免恢复与执行互锁。
            await db.scalar(select(AccessRevision).where(AccessRevision.id == 1).with_for_update())
            op = await db.get(IdentityOperation, identifier, with_for_update=True)
            require(
                op is not None and op.lease_owner == self.owner,
                409,
                "version_conflict",
                "操作已由其他执行器处理",
            )
            require(
                op.expires_at > datetime.now(UTC), 409, "version_conflict", "确认已过期，请重新核对"
            )
            access, domain = await self.authorized(db, op)
            employee = await db.get(Employee, op.employee_id) if op.employee_id else None
            if employee:
                require(
                    employee.version == op.expected_version,
                    409,
                    "version_conflict",
                    "人员状态已变化，请重新核对",
                )
            result = await callback(db, op, employee, access, domain)
            op.version += 1
            op.updated_at = datetime.now(UTC)
            if release:
                op.lease_owner = None
                op.lease_until = None
            access.audit(
                "account.completed" if op.status == "succeeded" else "account.step",
                op.id,
                {
                    "kind": op.kind,
                    "step": op.step,
                    "status": op.status,
                    "employee_id": op.employee_id,
                },
            )
            return result

    async def fail(self, identifier, message, code=""):
        async with self.sessions.begin() as db:
            op = await db.get(IdentityOperation, identifier, with_for_update=True)
            if op and op.lease_owner == self.owner:
                op.status = (
                    "needs_confirmation"
                    if code
                    in (
                        "version_conflict",
                        "permission_denied",
                        "inactive_employee",
                        "invalid_token",
                    )
                    else "needs_attention"
                )
                op.message = message[:240]
                op.lease_owner = None
                op.lease_until = None
                op.updated_at = datetime.now(UTC)
                op.version += 1
                access_audit = {"kind": op.kind, "step": op.step, "status": op.status}
                db.add(
                    Audit(
                        actor_id=op.request["executor_id"],
                        action="account.attention",
                        resource_id=op.id,
                        detail=access_audit,
                        trace_id=op.trace_id,
                    )
                )

    async def snapshot(self, identifier):
        async with self.sessions.begin() as db:
            op = await db.get(IdentityOperation, identifier)
            require(
                op is not None and op.lease_owner == self.owner,
                409,
                "version_conflict",
                "操作租约已变化",
            )
            require(op.expires_at > datetime.now(UTC), 409, "version_conflict", "确认已过期")
            await self.authorized(db, op)
            if op.employee_id:
                employee = await db.get(Employee, op.employee_id)
                require(
                    employee is not None and employee.version == op.expected_version,
                    409,
                    "version_conflict",
                    "人员信息已变化",
                )
            return op

    async def step(self, identifier):
        try:
            op = await self.snapshot(identifier)
            command = op.request["command"]
            kind = op.kind
            if op.step in ("mail_uncertain", "credential_uncertain"):
                raise DomainError(
                    409,
                    "constraint_conflict",
                    "上次敏感操作结果待核实，请确认后重新准备凭证，勿重复开户",
                )
            if op.step == "start" and kind in ("create", "link"):
                if kind == "create":
                    account = await self.provider.find_username(command["username"])
                    if account is None:
                        account = await self.provider.create(
                            command["username"], command["name"], command["email"], op.id
                        )
                    require(
                        account.operation_id == op.id,
                        409,
                        "constraint_conflict",
                        "账号已存在且不属于本次开户，请关联已有账号",
                    )
                else:
                    account = await self.provider.get(command["remote_id"])

                async def bind(db, row, employee, access, domain):
                    exists = await db.scalar(
                        select(IdentityLink.id).where(
                            IdentityLink.issuer == self.settings.issuer,
                            IdentityLink.subject == account.id,
                        )
                    )
                    require(
                        exists is None, 409, "constraint_conflict", "账号已绑定人员，请核对现有人员"
                    )
                    obj = Employee(name=command["name"], active=kind == "link" and account.enabled)
                    db.add(obj)
                    await db.flush()
                    db.add(
                        IdentityLink(
                            employee_id=obj.id,
                            issuer=self.settings.issuer,
                            subject=account.id,
                            managed=True,
                            login_name=account.username,
                        )
                    )
                    db.add_all(
                        [
                            EmployeeRole(employee_id=obj.id, role_id=value)
                            for value in command["role_ids"]
                        ]
                    )
                    row.employee_id, row.remote_id, row.expected_version = (
                        obj.id,
                        account.id,
                        obj.version,
                    )
                    if kind == "link":
                        row.status, row.step, row.message = (
                            "succeeded",
                            "done",
                            "已关联账号；"
                            + ("可按角色使用" if obj.active else "登录账号仍停用，需另行启用"),
                        )
                    else:
                        row.status = (
                            "awaiting_user" if command["mode"] == "temporary" else "pending"
                        )
                        row.step = "credential" if command["mode"] == "temporary" else "mail"
                        row.message = (
                            "账号已准备，请在受控页面准备临时密码"
                            if command["mode"] == "temporary"
                            else "正在准备设置密码邮件"
                        )
                    await access.changed(
                        obj.id,
                        {"kind": "employees", "operation": "create", "reason": "账号开通流程"},
                    )

                await self.local(identifier, bind)
                return

            if op.step == "start" and kind in ("reset", "logout"):
                account = await self.provider.get(op.remote_id)

                async def remember(db, row, employee, access, domain):
                    row.request = {**row.request, "was_enabled": account.enabled}
                    row.step = "suspend"
                    row.message = "已核实原登录状态，准备结束旧登录"

                # 原状态在外部变更前保存；停用后超时、重启不会误以为原本已停用。
                await self.local(identifier, remember)
                return

            if op.step == "suspend" and kind in ("reset", "logout"):
                await self.provider.update(op.remote_id, enabled=False)
                await self.provider.end_sessions(op.remote_id)

                async def prepared(db, row, employee, access, domain):
                    employee.tokens_after = int(time.time())
                    employee.version += 1
                    row.expected_version = employee.version
                    if kind == "logout":
                        row.status, row.step, row.message = (
                            "pending",
                            "activate",
                            "正在结束已有登录",
                        )
                    else:
                        row.status = (
                            "awaiting_user" if command["mode"] == "temporary" else "pending"
                        )
                        row.step = "credential" if command["mode"] == "temporary" else "mail"
                        row.message = (
                            "请在受控页面准备新临时密码"
                            if command["mode"] == "temporary"
                            else "正在准备密码邮件"
                        )

                await self.local(identifier, prepared)
                return

            if op.step == "mail":
                require(
                    self.settings.identity_management.email_enabled,
                    422,
                    "invalid_request",
                    "邮件未配置，请确认后改用临时密码",
                )
                account = await self.provider.get(op.remote_id)
                require(
                    bool(account.email), 422, "invalid_request", "账号未登记邮箱，请改用临时密码"
                )
                if kind == "reset":
                    require(
                        op.request.get("was_enabled"),
                        409,
                        "constraint_conflict",
                        "账号原本停用，请改用临时密码或保持停用处理",
                    )
                await self.provider.update(op.remote_id, enabled=True)

                async def sending(db, row, employee, access, domain):
                    row.step = "mail_uncertain"
                    row.status = "running"

                await self.local(identifier, sending, release=False)
                # 不释放租约，防止其他执行器在发信期间接管不确定步骤。
                await self.provider.password_email(op.remote_id)

                async def mailed(db, row, employee, access, domain):
                    row.status, row.step, row.message = (
                        "pending",
                        "activate",
                        "设置密码邮件已提交发送，正在完成开通",
                    )

                await self.local(identifier, mailed)
                return

            if op.step == "activate":
                enabled = True if kind == "create" else op.request.get("was_enabled", False)
                await self.provider.update(op.remote_id, enabled=enabled)
                # 安全操作在解除本地限制前再次结束可能存在的旧身份会话。
                if kind in ("reset", "logout"):
                    await self.provider.end_sessions(op.remote_id)

                async def activate(db, row, employee, access, domain):
                    if kind == "create":
                        employee.active = True
                    elif kind in ("reset", "logout"):
                        require(
                            employee.security_hold == row.id,
                            409,
                            "version_conflict",
                            "安全限制已变化，请重新核对",
                        )
                        employee.security_hold = None
                    employee.version += 1
                    row.expected_version = employee.version
                    row.status, row.step = "succeeded", "done"
                    row.message = (
                        "账号已开通，可按角色使用"
                        if kind == "create"
                        else "安全处理完成；仍按原启停状态与当前角色使用"
                    )
                    if command.get("mode") == "email":
                        row.message += "；设置密码邮件已提交发送"
                    await access.changed(
                        employee.id,
                        {"kind": "employees", "operation": "update", "reason": "账号操作完成"},
                    )

                await self.local(identifier, activate)
                return

            if op.step == "start" and kind in ("disable", "enable", "update"):
                if kind == "update":
                    await self.provider.update(
                        op.remote_id, name=command["name"], email=command.get("email")
                    )
                else:
                    await self.provider.update(op.remote_id, enabled=kind == "enable")
                    if kind == "disable":
                        await self.provider.end_sessions(op.remote_id)

                async def finish(db, row, employee, access, domain):
                    if kind == "update":
                        employee.name = command["name"]
                    else:
                        employee.active = kind == "enable"
                    employee.version += 1
                    row.expected_version = employee.version
                    row.status, row.step, row.message = (
                        "succeeded",
                        "done",
                        "人员资料已同步"
                        if kind == "update"
                        else (
                            "账号已重新启用，保留当前角色"
                            if kind == "enable"
                            else "业务访问与登录均已停用"
                        ),
                    )
                    await access.changed(
                        employee.id,
                        {"kind": "employees", "operation": "update", "reason": "账号操作完成"},
                    )

                await self.local(identifier, finish)
                return
            raise DomainError(409, "constraint_conflict", "此处理步骤需要管理员核对")
        except asyncio.CancelledError:
            raise
        except DomainError as error:
            await self.fail(identifier, error.message, error.code)
        except Exception:  # noqa: BLE001 — 不将上游响应或数据库正文写入操作记录
            await self.fail(identifier, "处理暂未完成，请核对进度后继续")

    async def credential(self, identifier, principal, trace, version):
        # 仅由受控Web路由调用，口令既不进入队列，也不作为公共业务输出。
        async with self.sessions.begin() as db:
            access = await Access.create(db, principal, trace, True)
            access.need("identity.credentials.manage")
            access.need("identity.read")
            access.need("access.read")
            op = await db.get(IdentityOperation, identifier, with_for_update=True)
            require(op is not None, 404, "not_found", "处理记录不存在")
            require(
                op.version == version and op.status == "awaiting_user" and op.step == "credential",
                409,
                "version_conflict",
                "处理进度已变化，请刷新核对",
            )
            require(
                op.kind in ("create", "reset"), 409, "constraint_conflict", "此操作不需要准备凭证"
            )
            require(
                op.expires_at > datetime.now(UTC),
                409,
                "version_conflict",
                "确认已过期，请先重新核对",
            )
            await self.authorized(db, op)
            target = await db.get(Employee, op.employee_id)
            require(
                target is not None and target.version == op.expected_version,
                409,
                "version_conflict",
                "人员状态已变化，请重新核对",
            )
            op.lease_owner = self.owner
            op.lease_until = datetime.now(UTC) + timedelta(seconds=60)
            op.status, op.step = "running", "credential_uncertain"
            op.version += 1
            op.updated_at = datetime.now(UTC)
            remote = op.remote_id
        password = secrets.token_urlsafe(24) + "Aa1!"
        try:
            await self.provider.temporary_password(remote, password)

            async def ready(db, row, employee, access, domain):
                row.status, row.step, row.message = (
                    "pending",
                    "activate",
                    "临时密码已准备，正在完成账号处理",
                )
                viewer = await Access.create(db, principal, trace, True)
                for permission in ("access.read", "identity.read", "identity.credentials.manage"):
                    viewer.need(permission)
                viewer.audit("account.credential", row.id, {"employee_id": row.employee_id})

            await self.local(identifier, ready)
        except DomainError as error:
            await self.fail(identifier, "密码设置结果待核实，请重新核对后生成", error.code)
            raise
        except Exception:  # noqa: BLE001 — 秘密步骤不得输出上游异常
            await self.fail(identifier, "密码设置结果待核实，请重新核对后生成")
            raise DomainError(
                503, "identity_unavailable", "密码准备结果待核实，请勿重复开户"
            ) from None
        self.wake.set()
        return password
