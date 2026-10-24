"""授权读模型：限定当前页批量计数，详情按现有关系查询，反向使用者分页。"""

from typing import Any

from sqlalchemy import Select, func, select

from app.common.errors import require
from app.infrastructure.models import (
    Employee,
    EmployeeRole,
    IdentityLink,
    IdentityOperation,
    Permission,
    PermissionSet,
    Role,
    RoleSet,
    SetPermission,
)
from app.infrastructure.pagination import numbered_rows
from app.modules.access.catalog import describe_permission


class Inspection:
    def __init__(self, access):
        self.access, self.db = access, access.db

    async def counts(self, kind, items):
        """只读取当前页对象；每种关系批量一次，不产生逐行查询。"""
        if kind not in {"employees", "roles", "permission-sets"}:
            return
        ids = [item["id"] for item in items]
        specifications: dict[str, list[tuple[str, Any, Any, Any]]] = {
            "employees": [("role_count", EmployeeRole.employee_id, EmployeeRole.role_id, None)],
            "roles": [
                ("permission_set_count", RoleSet.role_id, RoleSet.set_id, None),
                ("employee_count", EmployeeRole.role_id, EmployeeRole.employee_id, None),
                ("permission_count", RoleSet.role_id, SetPermission.permission_code, SetPermission),
            ],
            "permission-sets": [
                ("permission_count", SetPermission.set_id, SetPermission.permission_code, None),
                ("role_count", RoleSet.set_id, RoleSet.role_id, None),
            ],
        }
        for field, key, remote, join in specifications.get(kind, []):
            query = select(key, func.count(func.distinct(remote)))
            if join is not None:
                query = query.select_from(RoleSet).join(join, join.set_id == RoleSet.set_id)
            rows = (
                (await self.db.execute(query.where(key.in_(ids)).group_by(key))).all()
                if ids
                else []
            )
            counts = dict(rows)
            for item in items:
                item[field] = counts.get(item["id"], 0)

    async def employee_accounts(self, items, issuer):
        """仅为当前页批量提供账号摘要，不逐人调用身份HTTP。"""
        ids = [row["id"] for row in items]
        links = (
            (
                await self.db.scalars(
                    select(IdentityLink).where(
                        IdentityLink.employee_id.in_(ids), IdentityLink.issuer == issuer
                    )
                )
            ).all()
            if ids
            else []
        )
        mapping = {link.employee_id: link for link in links}
        operations = (
            (
                await self.db.scalars(
                    select(IdentityOperation)
                    .where(
                        IdentityOperation.employee_id.in_(ids),
                        IdentityOperation.status != "succeeded",
                    )
                    .order_by(IdentityOperation.created_at)
                )
            ).all()
            if ids
            else []
        )
        pending = {op.employee_id: op for op in operations}
        for item in items:
            link = mapping.get(item["id"])
            item["login_name"] = link.login_name if link else None
            item["managed"] = bool(link and link.managed)
            op = pending.get(item["id"])
            status = "已开通" if item["active"] else "已停用"
            if op:
                status = (
                    "需处理"
                    if op.status in ("needs_attention", "needs_confirmation")
                    else ("待准备凭证" if op.status == "awaiting_user" else "处理中")
                )
            item["account_status"] = status

    async def detail(
        self, kind: str, identifier: str, after: str = "", limit: int = 50, page: int | None = None
    ):
        self.access.need("access.read")
        model = {"employees": Employee, "roles": Role, "permission-sets": PermissionSet}[kind]
        obj = await self.db.get(model, identifier)
        require(obj is not None, 404, "not_found", "授权对象不存在")
        direct: Select[Any]
        permissions: Select[Any]
        used_by: dict
        if kind == "employees":
            direct = select(Role).join(EmployeeRole).where(EmployeeRole.employee_id == identifier)
            permissions = (
                select(Permission)
                .join(SetPermission)
                .join(RoleSet, RoleSet.set_id == SetPermission.set_id)
                .join(EmployeeRole, EmployeeRole.role_id == RoleSet.role_id)
                .where(EmployeeRole.employee_id == identifier)
            )
            used_by = {"items": [], "next": None, "has_more": False}
        elif kind == "roles":
            direct = select(PermissionSet).join(RoleSet).where(RoleSet.role_id == identifier)
            permissions = (
                select(Permission)
                .join(SetPermission)
                .join(RoleSet, RoleSet.set_id == SetPermission.set_id)
                .where(RoleSet.role_id == identifier)
            )
            used_by = await self._parents(
                Employee, EmployeeRole, EmployeeRole.role_id, identifier, after, limit, page
            )
        else:
            direct = (
                select(Permission).join(SetPermission).where(SetPermission.set_id == identifier)
            )
            permissions = direct
            used_by = await self._parents(
                Role, RoleSet, RoleSet.set_id, identifier, after, limit, page
            )
        linked = (await self.db.scalars(direct.limit(101))).all()
        # 每次替换最多100个直接关联；超出异常历史不悄悄截断，避免误删授权。
        require(
            len(linked) <= 100, 409, "constraint_conflict", "关联数量超出编辑上限，请联系管理员"
        )
        references = [
            {
                "id": row.code,
                "name": describe_permission(row.code)["name"],
                "description": row.description,
            }
            if isinstance(row, Permission)
            else {"id": row.id, "name": row.name}
            for row in linked
        ]
        granted = (await self.db.scalars(permissions.distinct().order_by(Permission.code))).all()
        return {
            "kind": kind,
            "builtin_key": obj.builtin_key if isinstance(obj, Role) else None,
            "id": obj.id,
            "name": obj.name,
            "version": obj.version,
            "active": obj.active if isinstance(obj, Employee) else None,
            "links": sorted(references, key=lambda row: (row["name"], row["id"])),
            "permissions": [describe_permission(row.code, row.description) for row in granted],
            "used_by": used_by,
        }

    async def _parents(self, model, association, foreign, identifier, after, limit, page=None):
        query = (
            select(model)
            .join(association)
            .where(foreign == identifier, model.id > after)
            .order_by(model.id)
        )
        pagination = None
        if page is not None:
            rows, pagination = await numbered_rows(self.db, query, page, limit)
        else:
            rows = (await self.db.scalars(query.limit(limit + 1))).all()
        return {
            "items": [{"id": row.id, "name": row.name} for row in rows[:limit]],
            "pagination": pagination,
            "has_more": pagination["page"] < pagination["pages"]
            if pagination
            else len(rows) > limit,
            "next": rows[limit - 1].id if len(rows) > limit else None,
        }
