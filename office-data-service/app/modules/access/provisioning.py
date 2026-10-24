"""首次开通的默认关联；不在登录、启用或启动路径补权。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import require
from app.infrastructure.models import EmployeeRole, Role


async def default_role(db: AsyncSession) -> Role:
    role = await db.scalar(select(Role).where(Role.builtin_key == "employee"))
    require(
        role is not None, 409, "constraint_conflict", "普通员工内置角色缺失，请管理员检查数据库迁移"
    )
    assert role is not None
    return role


async def assign_role(db: AsyncSession, employee_id: str, enabled: bool) -> dict | None:
    if not enabled:
        return None
    role = await default_role(db)
    db.add(EmployeeRole(employee_id=employee_id, role_id=role.id))
    return {"id": role.id, "name": role.name, "description": "首次开通关联的默认角色"}
