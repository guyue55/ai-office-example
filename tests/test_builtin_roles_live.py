"""默认开通的真实 PG 验证；测试身份独立，不修改既有员工授权。"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.database import async_sessions
from app.infrastructure.models import Audit, Employee, EmployeeRole, IdentityLink, Role
from sqlalchemy import func, select


def test_builtin_creation_optout_revocation_and_atomicity(live):
    settings = live["settings"]
    operator_id = live["operator"].request("GET", "/api/v2/me")["id"]

    async def run():
        sessions = async_sessions(settings)
        app = Application(settings, sessions, None)
        try:
            async with sessions.begin() as db:
                subject = await db.scalar(
                    select(IdentityLink.subject).where(
                        IdentityLink.employee_id == operator_id,
                        IdentityLink.issuer == settings.issuer,
                    )
                )
                builtin = (
                    await db.scalars(select(Role).where(Role.builtin_key.is_not(None)))
                ).all()
                assert (
                    {"employee", "access-admin"}
                    <= {row.builtin_key for row in builtin}
                    <= {"employee", "access-admin", "identity-admin", "session-admin"}
                )
                employee_role_id = next(row.id for row in builtin if row.builtin_key == "employee")
            operator = SimpleNamespace(issuer=settings.issuer, subject=subject, claims={})

            async def call(operation, payload, principal=operator):
                return await app.call(principal, operation, payload, str(uuid4()))

            body = {"name": "默认开通验证", "issuer": settings.issuer, "subject": uuid4().hex}
            change = {"operation": "access.create", "payload": {"kind": "employees", "body": body}}
            preview = await call("access.change.preview", {"change": change})
            assert preview["after"]["assigned_role"]["id"] == employee_role_id
            assert {p["code"] for p in preview["after"]["permissions"]} == set(live["permissions"])
            assert preview["added"][0]["id"] == employee_role_id
            created = await call(
                "access.change.apply",
                {"change": change, "confirmation_digest": preview["confirmation_digest"]},
            )
            assert created["assigned_role"]["id"] == employee_role_id
            user = SimpleNamespace(issuer=settings.issuer, subject=body["subject"], claims={})
            identity = await call("me", {}, user)
            assert set(identity["permissions"]) == set(live["permissions"])
            # 本人角色随身份一并返回，普通员工无需 access.read 也能看到自己的角色。
            assert [row["name"] for row in identity["roles"]] == ["普通员工"]
            assert identity["roles"][0]["builtin_key"] == "employee"
            assert identity["roles"][0]["permission_count"] == len(live["permissions"])
            with pytest.raises(DomainError) as caught:
                await call("access.list", {"kind": "roles"}, user)
            assert caught.value.code == "permission_denied"
            # 取消默认授权，预览与执行均不携带隐式关联。
            optout_body = {**body, "subject": uuid4().hex, "assign_default_role": False}
            optout_change = {
                "operation": "access.create",
                "payload": {"kind": "employees", "body": optout_body},
            }
            optout_preview = await call("access.change.preview", {"change": optout_change})
            assert not optout_preview["added"] and "assigned_role" not in optout_preview["after"]
            optout = await call("access.create", optout_change["payload"])
            assert optout["assigned_role"] is None
            optout_user = SimpleNamespace(
                issuer=settings.issuer, subject=optout_body["subject"], claims={}
            )
            assert (await call("me", {}, optout_user))["permissions"] == []
            assert (await call("me", {}, optout_user))["roles"] == []
            # 实际撤权后，重新启用以及重新构造应用均不会补回。
            await call(
                "access.links.replace",
                {
                    "kind": "employees",
                    "identifier": created["id"],
                    "relation": "roles",
                    "body": {"version": 1, "ids": []},
                },
            )
            await call(
                "access.employee.update",
                {
                    "identifier": created["id"],
                    "body": {"version": 2, "name": body["name"], "active": False},
                },
            )
            await call(
                "access.employee.update",
                {
                    "identifier": created["id"],
                    "body": {"version": 3, "name": body["name"], "active": True},
                },
            )
            app = Application(settings, sessions, None)
            assert (await call("me", {}, user))["permissions"] == []
            assert (await call("me", {}, user))["roles"] == []
            with pytest.raises(DomainError) as caught:
                await call(
                    "access.change.apply",
                    {"change": change, "confirmation_digest": preview["confirmation_digest"]},
                )
            assert caught.value.code == "version_conflict"
            # 相同身份重复开通触发约束，新增员工和角色关系必须完整回滚。
            async with sessions.begin() as db:
                count_before = await db.scalar(select(func.count()).select_from(Employee))
            with pytest.raises(DomainError) as caught:
                await call("access.create", {"kind": "employees", "body": body})
            assert caught.value.code == "constraint_conflict"
            async with sessions.begin() as db:
                assert await db.scalar(select(func.count()).select_from(Employee)) == count_before
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(EmployeeRole)
                        .where(EmployeeRole.employee_id == created["id"])
                    )
                    == 0
                )
                audit = await db.scalar(
                    select(Audit)
                    .where(Audit.resource_id == created["id"], Audit.action == "access.changed")
                    .order_by(Audit.created_at)
                )
                assert audit.detail["assigned_role"]["id"] == employee_role_id
        finally:
            await sessions.kw["bind"].dispose()

    asyncio.run(run())


def test_seed_downgrade_upgrade_preserves_customized_grants(live):
    """DDL 和定制均在 owner 事务内验证后回滚，不改变开发库当前状态。"""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.infrastructure.config import Settings
    from app.infrastructure.database import create_database_engine
    from sqlalchemy import text

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "builtin_migration", root / "office-data-service/migrations/versions/0006_builtin_roles.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    settings = Settings.load(root / "runtime/foundation/owner.json")
    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                role_id = connection.scalar(
                    text("SELECT id FROM office.role WHERE builtin_key='employee'")
                )
                set_id = connection.scalar(
                    text("SELECT set_id FROM office.role_permission_set WHERE role_id=:id"),
                    {"id": role_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM office.permission_set_permission WHERE set_id=:id AND permission_code='record.create'"
                    ),
                    {"id": set_id},
                )
                people_before = connection.execute(
                    text(
                        "SELECT employee_id,role_id FROM office.employee_role ORDER BY employee_id,role_id"
                    )
                ).all()
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    migration.downgrade()
                    migration.upgrade()
                assert (
                    connection.scalar(
                        text("SELECT builtin_key FROM office.role WHERE id=:id"), {"id": role_id}
                    )
                    == "employee"
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM office.permission_set_permission WHERE set_id=:id AND permission_code='record.create'"
                        ),
                        {"id": set_id},
                    )
                    == 0
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT employee_id,role_id FROM office.employee_role ORDER BY employee_id,role_id"
                        )
                    ).all()
                    == people_before
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
