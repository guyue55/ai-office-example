"""独立临时PG验证外部身份分页、确认与授权边界；不读取既有实例。"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.database import async_sessions
from app.infrastructure.models import Employee, IdentityLink, PendingIdentity
from sqlalchemy import select
from test_query_postgres import isolated_query_db, principal  # noqa: F401

pytestmark = pytest.mark.live


def test_pending_pagination_and_confirmed_external_binding(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory = async_sessions(settings)
        application = Application(settings, factory, None)
        admin = principal(settings, "operator")

        async def call(operation, payload, who=admin):
            return await application.call(who, operation, payload, uuid4().hex)

        try:
            now = datetime.now(UTC)
            ids = [str(uuid4()) for _ in range(63)]
            async with factory.begin() as db:
                employee = Employee(name="隔离已开通人员", active=False)
                db.add(employee)
                await db.flush()
                db.add(
                    IdentityLink(employee_id=employee.id, issuer=settings.issuer, subject="linked")
                )
                db.add_all(
                    [
                        PendingIdentity(
                            id=identifier,
                            issuer=settings.issuer,
                            subject=f"test-{index}",
                            display_name="含%字面" if index == 0 else "合成访问者",
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                        for index, identifier in enumerate(ids)
                    ]
                )
                db.add_all(
                    [
                        PendingIdentity(
                            issuer=settings.issuer,
                            subject="linked",
                            display_name="停用也不重复入队",
                        ),
                        PendingIdentity(
                            issuer=settings.issuer + "/other",
                            subject="foreign",
                            display_name="其他来源",
                        ),
                        PendingIdentity(
                            issuer=settings.issuer,
                            subject="claimed",
                            display_name="已处理",
                            claimed_by=employee.id,
                        ),
                    ]
                )
            pages = [
                await call("access.identity.pending", {"page": page, "limit": 20})
                for page in range(1, 5)
            ]
            assert all(page["pagination"]["total"] == 63 for page in pages)
            assert [row["id"] for page in pages for row in page["items"]] == sorted(ids)
            assert (await call("access.identity.pending", {"page": 999, "limit": 20}))[
                "pagination"
            ]["page"] == 4
            literal = await call("access.identity.pending", {"q": "%"})
            assert literal["pagination"]["total"] == 1
            assert (await call("access.identity.pending", {"q": "no-match"}))["pagination"][
                "total"
            ] == 0
            change = {
                "operation": "access.identity.claim",
                "payload": {
                    "pending_id": ids[0],
                    "name": "核对后开通",
                    "assign_default_role": True,
                },
            }
            preview = await call("access.change.preview", {"change": change})
            assert preview["before"]["issuer"] == settings.issuer
            assert preview["before"]["subject"] == "test-0"
            assert preview["after"]["assigned_role"]
            async with factory() as db:
                assert (await db.get(PendingIdentity, ids[0])).claimed_by is None
            with pytest.raises(DomainError) as rejected:
                await call(
                    "access.change.apply", {"change": change, "confirmation_digest": "0" * 64}
                )
            assert rejected.value.status == 409
            result = await call(
                "access.change.apply",
                {"change": change, "confirmation_digest": preview["confirmation_digest"]},
            )
            assert result["assigned_role"]
            async with factory() as db:
                link = await db.scalar(
                    select(IdentityLink).where(IdentityLink.employee_id == result["id"])
                )
                assert link.managed is False
            with pytest.raises(DomainError):
                await call(
                    "access.change.apply",
                    {"change": change, "confirmation_digest": preview["confirmation_digest"]},
                )
            assert (await call("access.identity.pending", {}))["pagination"]["total"] == 62
            with pytest.raises(DomainError) as denied:
                await call("access.identity.pending", {}, principal(settings, "test-0"))
            assert denied.value.status == 403
            # 手填路径同样保持非受管，且不会给没有绑定的登录者自动授予角色。
            subject = "manual-" + uuid4().hex
            change = {
                "operation": "access.create",
                "payload": {
                    "kind": "employees",
                    "body": {
                        "issuer": settings.issuer,
                        "subject": subject,
                        "name": "手填合成人员",
                        "assign_default_role": True,
                    },
                },
            }
            preview = await call("access.change.preview", {"change": change})
            result = await call(
                "access.change.apply",
                {"change": change, "confirmation_digest": preview["confirmation_digest"]},
            )
            async with factory() as db:
                link = await db.scalar(
                    select(IdentityLink).where(IdentityLink.employee_id == result["id"])
                )
                assert link.managed is False
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())


def test_account_onboarding_roles_execution_and_revocation(request, tmp_path):
    """真实迁移库＋合成身份端口：默认开通、空角色拒绝、撤权不补回。"""
    from app.infrastructure.config import IdentityManagement
    from app.infrastructure.models import EmployeeRole, Role
    from app.modules.access.identity_ports import LoginAccount
    from test_account_recovery_live import IdentityBoundary

    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory = async_sessions(settings)
        application = Application(settings, factory, None)
        accounts = application.account_service()
        boundary = IdentityBoundary()
        boundary.fail_create = False
        accounts.provider = accounts.runner.provider = boundary
        # 仅供执行器读取公开策略；替身端口不会读取秘密或发送HTTP。
        accounts.runner.settings = settings.model_copy(
            update={
                "identity_management": IdentityManagement(
                    client_id="synthetic", client_secret_file=tmp_path / "never-created"
                )
            }
        )
        admin = principal(settings, "operator")

        async def call(operation, payload, who=admin):
            return await application.call(who, operation, payload, uuid4().hex)

        async def submit(command):
            preview = await call("account.preview", {"request": command})
            result = await call(
                "account.submit",
                {
                    "request": preview["request"],
                    "confirmation_digest": preview["confirmation_digest"],
                    "idempotency_key": uuid4().hex,
                },
            )
            return preview, result

        try:
            async with factory.begin() as db:
                builtin = await db.scalar(select(Role).where(Role.builtin_key == "employee"))
                default_id = builtin.id
                custom = Role(name="专用查阅角色")
                db.add(custom)
                await db.flush()
                custom_id = custom.id
            # 缺省及null解析为同一默认角色；显式其他角色保持原选择，不叠加权限。
            create = {
                "kind": "create",
                "name": "合成新员工",
                "username": "local-" + uuid4().hex[:12],
            }
            preview, created = await submit(create)
            assert preview["request"]["role_ids"] == [default_id]
            assert await accounts.runner.claim(created["id"]) == created["id"]
            await accounts.runner.step(created["id"])
            result = await call("account.operation", {"identifier": created["id"]})
            assert result["status"] == "awaiting_user"  # 尚未准备凭证，不能宣称开户全部完成。
            async with factory() as db:
                ids = (
                    await db.scalars(
                        select(EmployeeRole.role_id).where(
                            EmployeeRole.employee_id == result["employee_id"]
                        )
                    )
                ).all()
                assert ids == [default_id]
            for role_ids in (None, [custom_id]):
                remote_id = uuid4().hex
                boundary.accounts[remote_id] = LoginAccount(
                    remote_id, "linked-" + remote_id[:8], "合成既有账号", "", True, (), None
                )
                preview, linked = await submit(
                    {
                        "kind": "link",
                        "name": "合成关联员工",
                        "remote_id": remote_id,
                        "role_ids": role_ids,
                    }
                )
                expected = [default_id] if role_ids is None else [custom_id]
                assert preview["request"]["role_ids"] == expected
                assert await accounts.runner.claim(linked["id"]) == linked["id"]
                await accounts.runner.step(linked["id"])
                result = await call("account.operation", {"identifier": linked["id"]})
                assert result["status"] == "succeeded"
                person = principal(settings, remote_id)
                assert [r["id"] for r in (await call("me", {}, person))["roles"]] == expected
                await call(
                    "access.links.replace",
                    {
                        "kind": "employees",
                        "identifier": result["employee_id"],
                        "relation": "roles",
                        "body": {"version": 1, "ids": []},
                    },
                )
                for kind in ("disable", "enable"):
                    detail = await call(
                        "access.detail", {"kind": "employees", "identifier": result["employee_id"]}
                    )
                    _, operation = await submit(
                        {
                            "kind": kind,
                            "identifier": result["employee_id"],
                            "version": detail["version"],
                        }
                    )
                    assert await accounts.runner.claim(operation["id"]) == operation["id"]
                    await accounts.runner.step(operation["id"])
                    completed = await call("account.operation", {"identifier": operation["id"]})
                    assert completed["status"] == "succeeded"
                # 重建应用模拟重启，新的登录仍不能补回撤销的角色。
                fresh = Application(settings, factory, None)
                with pytest.raises(DomainError) as revoked:
                    await fresh.call(person, "me", {}, uuid4().hex)
                assert revoked.value.status == 401
                async with factory() as db:
                    employee = await db.get(Employee, result["employee_id"])
                    # 合成新令牌模拟本人重新登录；不复用已撤销的令牌。
                    person.claims["iat"] = employee.tokens_after + 1
                me = await fresh.call(person, "me", {}, uuid4().hex)
                assert me["roles"] == [] and me["permissions"] == []
            for command in (create, {"kind": "link", "name": "无角色", "remote_id": "unlinked"}):
                with pytest.raises(DomainError) as rejected:
                    await call("account.preview", {"request": {**command, "role_ids": []}})
                assert rejected.value.status == 422
            # 默认角色丢失时阻止开通，不能静默降级为零角色。
            async with factory.begin() as db:
                builtin = await db.get(Role, default_id)
                builtin.builtin_key = None
            try:
                with pytest.raises(DomainError) as rejected:
                    await call("account.preview", {"request": create})
                assert rejected.value.status == 409
            finally:
                async with factory.begin() as db:
                    builtin = await db.get(Role, default_id)
                    builtin.builtin_key = "employee"
            # 预览后角色版本变化：旧摘要不得提交，未进入外部创建阶段。
            preview = await call("account.preview", {"request": create})
            async with factory.begin() as db:
                builtin = await db.get(Role, default_id)
                builtin.version += 1
            with pytest.raises(DomainError) as rejected:
                await call(
                    "account.submit",
                    {
                        "request": preview["request"],
                        "confirmation_digest": preview["confirmation_digest"],
                        "idempotency_key": uuid4().hex,
                    },
                )
            assert rejected.value.status == 409
            assert boundary.creates == 1
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
