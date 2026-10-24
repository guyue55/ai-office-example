"""真实PG事务中的故障注入；外部副作用以独立有状态端口模拟，测试后整体回滚。"""

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.application.account_runner import AccountRunner
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.database import async_sessions
from app.infrastructure.models import Employee, IdentityLink, IdentityOperation, Role
from app.modules.access.identity_ports import LoginAccount
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


class IdentityBoundary:
    def __init__(self):
        self.accounts = {}
        self.creates = 0
        self.passwords = 0
        self.fail_create = True
        self.fail_password = True
        self.fail_logout = False

    async def find_username(self, username):
        return next((a for a in self.accounts.values() if a.username == username), None)

    async def get(self, identifier):
        return self.accounts[identifier]

    async def create(self, username, name, email, operation_id):
        self.creates += 1
        account = LoginAccount(
            str(uuid4()), username, name, email, False, ("UPDATE_PASSWORD",), operation_id
        )
        self.accounts[account.id] = account
        if self.fail_create:
            self.fail_create = False
            raise DomainError(503, "identity_unavailable", "外部已创建但响应超时")
        return account

    async def update(self, identifier, **changes):
        selected = {key: value for key, value in changes.items() if value is not None}
        self.accounts[identifier] = replace(self.accounts[identifier], **selected)

    async def temporary_password(self, identifier, password):
        self.passwords += 1
        if self.fail_password:
            self.fail_password = False
            raise DomainError(503, "identity_unavailable", "设置结果待核实")

    async def end_sessions(self, identifier):
        if self.fail_logout:
            self.fail_logout = False
            raise DomainError(503, "identity_unavailable", "结束会话超时")

    async def aclose(self):
        pass


def test_unknown_results_recovery_expiry_and_lease_ownership(live):
    settings = live["settings"]
    operator = live["operator"].me()["id"]

    async def scenario():
        factory = async_sessions(settings)
        engine = factory.kw["bind"]
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                sessions = async_sessionmaker(
                    connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
                )
                app = Application(settings, sessions, None)
                accounts = app.account_service()
                await accounts.provider.aclose()
                boundary = IdentityBoundary()
                accounts.provider = accounts.runner.provider = boundary
                runner = accounts.runner
                async with sessions.begin() as db:
                    subject = await db.scalar(
                        select(IdentityLink.subject).where(
                            IdentityLink.employee_id == operator,
                            IdentityLink.issuer == settings.issuer,
                        )
                    )
                principal = SimpleNamespace(
                    issuer=settings.issuer, subject=subject, claims={"iat": int(time.time())}
                )

                async def call(operation, payload):
                    return await app.call(principal, "account." + operation, payload, str(uuid4()))

                async def read(identifier):
                    return await call("operation", {"identifier": identifier})

                async def step(identifier):
                    claimed = await runner.claim(identifier)
                    assert claimed == identifier
                    await runner.step(identifier)
                    return await read(identifier)

                async def resume(op):
                    preview = await call("resume_preview", {"identifier": op["id"]})
                    return await call(
                        "resume",
                        {
                            "identifier": op["id"],
                            "version": op["version"],
                            "confirmation_digest": preview["confirmation_digest"],
                        },
                    )

                async def submit(command):
                    preview = await call("preview", {"request": command})
                    return await call(
                        "submit",
                        {
                            "request": preview["request"],
                            "confirmation_digest": preview["confirmation_digest"],
                            "idempotency_key": uuid4().hex,
                        },
                    )

                try:
                    op = await submit(
                        {
                            "kind": "create",
                            "name": "故障注入",
                            "username": "recovery-" + uuid4().hex[:12],
                        }
                    )
                    failed = await step(op["id"])
                    assert failed["status"] == "needs_attention"
                    assert failed["employee_id"] is None and boundary.creates == 1
                    await resume(failed)
                    waiting = await step(op["id"])
                    assert waiting["status"] == "awaiting_user" and boundary.creates == 1
                    async with sessions.begin() as db:
                        target = await db.get(Employee, waiting["employee_id"])
                        target.name = "并发核实后的姓名"
                        target.version += 1
                    with pytest.raises(DomainError) as stale:
                        await runner.credential(
                            op["id"], principal, str(uuid4()), waiting["version"]
                        )
                    assert stale.value.code == "version_conflict" and boundary.passwords == 0
                    # 已绑定开户也能核对最新人员版本后续办，不困在永久版本冲突。
                    updated_preview = await call("resume_preview", {"identifier": op["id"]})
                    assert "并发核实后的姓名" in " ".join(updated_preview["effects"])
                    await resume(await read(op["id"]))
                    waiting = await read(op["id"])
                    with pytest.raises(DomainError):
                        await runner.credential(
                            op["id"], principal, str(uuid4()), waiting["version"]
                        )
                    uncertain = await read(op["id"])
                    assert (
                        uncertain["status"] == "needs_attention"
                        and uncertain["step"] == "credential_uncertain"
                    )
                    assert await runner.claim(op["id"]) is None
                    await resume(uncertain)
                    waiting = await read(op["id"])
                    secret = await runner.credential(
                        op["id"], principal, str(uuid4()), waiting["version"]
                    )
                    assert len(secret) > 24 and boundary.passwords == 2
                    completed = await step(op["id"])
                    assert completed["status"] == "succeeded"
                    person = await call("detail", {"identifier": completed["employee_id"]})
                    assert person["active"] and person["account"]["enabled"]

                    # 原启用状态必须先持久化，停用之后外部超时也能正确恢复。
                    logout = await submit(
                        {
                            "kind": "logout",
                            "identifier": person["employee_id"],
                            "version": person["version"],
                        }
                    )
                    remembered = await step(logout["id"])
                    assert remembered["step"] == "suspend"
                    boundary.fail_logout = True
                    failed = await step(logout["id"])
                    assert failed["status"] == "needs_attention"
                    assert not (await boundary.get(person["account"]["id"])).enabled
                    await resume(failed)
                    await step(logout["id"])
                    result = await step(logout["id"])
                    assert result["status"] == "succeeded"
                    person = await call("detail", {"identifier": completed["employee_id"]})
                    assert (
                        person["active"]
                        and person["account"]["enabled"]
                        and not person["security_pending"]
                    )

                    # 等待人工步骤过期后进入重新确认，不能永久困在失效按钮。
                    second = await submit(
                        {
                            "kind": "create",
                            "name": "超时验证",
                            "username": "expiry-" + uuid4().hex[:12],
                        }
                    )
                    waiting = await step(second["id"])
                    async with sessions.begin() as db:
                        row = await db.get(IdentityOperation, second["id"])
                        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    assert await runner.claim(second["id"]) is None
                    expired = await read(second["id"])
                    assert expired["status"] == "needs_confirmation"
                    await resume(expired)
                    assert (await read(second["id"]))["status"] == "awaiting_user"

                    # 角色修改后不得沿用旧预览静默授予新能力。
                    third = await submit(
                        {
                            "kind": "create",
                            "name": "角色变更",
                            "username": "roles-" + uuid4().hex[:12],
                        }
                    )
                    async with sessions.begin() as db:
                        role = await db.scalar(select(Role).where(Role.builtin_key == "employee"))
                        role.version += 1
                    result = await step(third["id"])
                    assert result["status"] == "needs_confirmation"
                    assert result["employee_id"] is None

                    fourth = await submit(
                        {
                            "kind": "create",
                            "name": "租约验证",
                            "username": "lease-" + uuid4().hex[:12],
                        }
                    )
                    assert await runner.claim(fourth["id"]) == fourth["id"]
                    other = AccountRunner(settings, sessions, boundary)
                    assert await other.claim(fourth["id"]) is None
                    async with sessions.begin() as db:
                        row = await db.get(IdentityOperation, fourth["id"])
                        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                    assert await other.claim(fourth["id"]) == fourth["id"]
                    await runner.step(fourth["id"])
                    async with sessions.begin() as db:
                        row = await db.get(IdentityOperation, fourth["id"])
                        assert row.lease_owner == other.owner and row.status == "running"
                    await other.step(fourth["id"])
                    assert (await read(fourth["id"]))["status"] == "awaiting_user"

                    fifth = await submit(
                        {
                            "kind": "create",
                            "name": "操作者撤权",
                            "username": "actor-" + uuid4().hex[:12],
                        }
                    )
                    async with sessions.begin() as db:
                        actor = await db.get(Employee, operator)
                        actor.active = False
                    assert await runner.claim(fifth["id"]) == fifth["id"]
                    await runner.step(fifth["id"])
                    async with sessions.begin() as db:
                        row = await db.get(IdentityOperation, fifth["id"])
                        assert row.status == "needs_confirmation" and row.employee_id is None
                        actor = await db.get(Employee, operator)
                        actor.active = True
                finally:
                    await accounts.runner.close()
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
