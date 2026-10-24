"""认领事务的真实 PostgreSQL 回归；仅使用显式启用的独立开发环境。"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.database import async_sessions
from app.infrastructure.models import Employee, IdentityLink, PendingIdentity
from sqlalchemy import func, select


@pytest.mark.parametrize("assign_default", [True, False])
def test_claim_chain_with_one_connection_and_concurrent_first_visits(live, assign_default):
    base = live["settings"]
    settings = base.model_copy(
        update={
            "operation_timeout": 5,
            "database_runtime": base.database_runtime.model_copy(
                update={
                    "pool_size": 1,
                    "max_overflow": 0,
                    "pool_timeout": 0.5,
                }
            ),
        }
    )
    operator_id = live["operator"].request("GET", "/api/v2/me")["id"]

    async def run():
        factory = async_sessions(settings)
        application = Application(settings, factory, None)
        newcomer = SimpleNamespace(
            issuer=settings.issuer, subject="review-" + uuid4().hex, claims={"name": "复审核对员工"}
        )
        try:
            # 与真实登录后相同的应用入口；同一未知身份并发请求只能有一条登记。
            async def first_visit():
                with pytest.raises(DomainError) as caught:
                    await application.call(newcomer, "project.list", {}, str(uuid4()))
                assert caught.value.code == "inactive_employee"

            await asyncio.gather(*(first_visit() for _ in range(4)))
            async with factory.begin() as db:
                rows = (
                    await db.scalars(
                        select(PendingIdentity).where(PendingIdentity.subject == newcomer.subject)
                    )
                ).all()
                assert len(rows) == 1
                pending_id = rows[0].id
                operator_subject = await db.scalar(
                    select(IdentityLink.subject)
                    .join(Employee, Employee.id == IdentityLink.employee_id)
                    .where(Employee.id == operator_id, IdentityLink.issuer == settings.issuer)
                )
                employee_subject = await db.scalar(
                    select(IdentityLink.subject).where(
                        IdentityLink.employee_id == live["employee_id"],
                        IdentityLink.issuer == settings.issuer,
                    )
                )
            assert operator_subject and employee_subject
            ordinary = SimpleNamespace(issuer=settings.issuer, subject=employee_subject, claims={})
            with pytest.raises(DomainError) as denied:
                await application.call(
                    ordinary,
                    "access.identity.claim",
                    {"pending_id": pending_id, "name": "越权认领"},
                    str(uuid4()),
                )
            assert denied.value.code == "permission_denied"
            operator = SimpleNamespace(issuer=settings.issuer, subject=operator_subject, claims={})
            readonly = SimpleNamespace(
                issuer=settings.issuer,
                subject=newcomer.subject,
                claims={},
            )
            # 未开通身份不能自认领。
            with pytest.raises(DomainError) as denied:
                await application.call(
                    readonly,
                    "access.identity.claim",
                    {"pending_id": pending_id, "name": "自认领"},
                    str(uuid4()),
                )
            assert denied.value.code == "inactive_employee"

            async def claim():
                try:
                    return await application.call(
                        operator,
                        "access.identity.claim",
                        {
                            "pending_id": pending_id,
                            "name": "复审认领-" + newcomer.subject,
                            "assign_default_role": assign_default,
                        },
                        str(uuid4()),
                    )
                except DomainError as error:
                    assert error.code == "identity_claimed"
                    return None

            results = await asyncio.gather(claim(), claim())
            successful = [result for result in results if result]
            assert len(successful) == 1
            employee_id = successful[0]["id"]
            identity = await application.call(newcomer, "me", {}, str(uuid4()))
            assert identity["id"] == employee_id
            assert set(identity["permissions"]) == (
                set(live["permissions"]) if assign_default else set()
            )
            # 普通员工可查项目，但不能管理授权或读取他人数据。
            if assign_default:
                await application.call(newcomer, "project.list", {}, str(uuid4()))
            else:
                with pytest.raises(DomainError) as denied:
                    await application.call(newcomer, "project.list", {}, str(uuid4()))
                assert denied.value.code == "permission_denied"
            with pytest.raises(DomainError) as denied:
                await application.call(newcomer, "access.list", {"kind": "employees"}, str(uuid4()))
            assert denied.value.code == "permission_denied"
            async with factory.begin() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(IdentityLink)
                        .where(IdentityLink.subject == newcomer.subject)
                    )
                    == 1
                )
            await application.call(
                operator,
                "access.employee.update",
                {
                    "identifier": employee_id,
                    "body": {"version": 1, "name": identity["name"], "active": False},
                },
                str(uuid4()),
            )
            with pytest.raises(DomainError) as denied:
                await application.call(newcomer, "me", {}, str(uuid4()))
            assert denied.value.code == "inactive_employee"
            pending = await application.call(operator, "access.identity.pending", {}, str(uuid4()))
            assert pending_id not in {item["id"] for item in pending["items"]}
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(run())


def test_audit_chinese_action_search(live):
    response = live["operator"].request("GET", "/api/v2/access/audit", params={"q": "授权配置变更"})
    assert response["items"]
    assert all(row["action"] == "access.changed" for row in response["items"])
    assert all(
        row["actor_name"] and row["action_name"] == "授权配置变更" for row in response["items"]
    )
