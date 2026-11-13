"""隔离PG验证列表摘要的截断、计数和当前页范围。"""

import asyncio
from types import SimpleNamespace

import pytest
from app.infrastructure.database import async_sessions
from app.infrastructure.models import (
    Employee,
    EmployeeRole,
    Permission,
    PermissionSet,
    Role,
    RoleSet,
    SetPermission,
)
from app.modules.access.inspection import Inspection
from sqlalchemy import select
from test_query_postgres import isolated_query_db  # noqa: F401

pytestmark = pytest.mark.live


def test_bounded_current_page_summaries(isolated_query_db):  # noqa: F811
    async def run():
        factory = async_sessions(isolated_query_db)
        try:
            async with factory() as db:
                employee = await db.scalar(select(Employee).limit(1))
                bundle = PermissionSet(name="摘要隔离测试")
                db.add(bundle)
                roles = [Role(name=f"摘要角色{i}") for i in range(5)]
                db.add_all(roles)
                await db.flush()
                for role in roles:
                    db.add(EmployeeRole(employee_id=employee.id, role_id=role.id))
                    db.add(RoleSet(set_id=bundle.id, role_id=role.id))
                codes = list(
                    (
                        await db.scalars(select(Permission.code).order_by(Permission.code).limit(5))
                    ).all()
                )
                assert len(codes) == 5
                for code in codes:
                    db.add(SetPermission(set_id=bundle.id, permission_code=code))
                await db.flush()
                inspector = Inspection(SimpleNamespace(db=db))
                items = [{"id": bundle.id}, {"id": "not-on-this-page"}]
                await inspector.counts("permission-sets", items)
                assert items[0]["role_count"] == 5
                assert items[0]["permission_count"] == 5
                assert items[0]["role_names"] == [f"摘要角色{i}" for i in range(3)]
                assert len(items[0]["permission_names"]) == 3
                assert items[1]["role_names"] == []
                assert items[1]["permission_names"] == []
                people = [{"id": employee.id}]
                await inspector.counts("employees", people)
                assert len(people[0]["role_names"]) == 3
                assert people[0]["role_count"] >= 5
                await db.rollback()
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(run())
