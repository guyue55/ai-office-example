"""人员与权限配置服务；不依赖 HTTP、MCP 或固定角色名称。"""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select

from app.common.errors import require
from app.infrastructure.models import (
    Audit,
    ClientAuthorization,
    Employee,
    EmployeeRole,
    IdentityLink,
    IdentityOperation,
    PendingIdentity,
    Permission,
    PermissionSet,
    Project,
    Role,
    RoleSet,
    SetPermission,
)
from app.infrastructure.pagination import numbered_rows
from app.modules.access.catalog import (
    AUDIT_ACTIONS,
    DEFINITIONS,
    describe_audit_action,
    describe_audit_detail,
    describe_permission,
)
from app.modules.access.inspection import Inspection
from app.modules.access.provisioning import assign_role
from app.modules.access.schemas import (
    EmployeeUpdate,
    GrantUpdate,
    Links,
    Named,
    NewEmployee,
)


def view(obj, fields):
    return {key: getattr(obj, key) for key in fields}


class Management:
    def __init__(self, access, issuer: str):
        self.access, self.db, self.issuer = access, access.db, issuer

    async def me(self):
        codes = sorted(
            p.code
            for p in (await self.db.scalars(select(Permission)))
            if self.access.allows(p.code)
        )
        return {
            **view(self.access.actor, ("id", "name", "active", "version")),
            "permissions": codes,
            "permission_details": [describe_permission(code) for code in codes],
            "access_revision": self.access.revision.revision,
            "roles": await self._my_roles(),
        }

    async def _my_roles(self):
        """本人已关联的角色及各自配置的能力数量；不读取其他员工信息。"""
        rows = (
            await self.db.execute(
                select(Role.id, Role.name, Role.builtin_key)
                .join(EmployeeRole, EmployeeRole.role_id == Role.id)
                .where(EmployeeRole.employee_id == self.access.actor.id)
                .order_by(Role.name, Role.id)
            )
        ).all()
        roles = [{"id": row.id, "name": row.name, "builtin_key": row.builtin_key} for row in rows]
        if not roles:
            return roles
        counts = dict(
            (
                await self.db.execute(
                    select(
                        RoleSet.role_id, func.count(func.distinct(SetPermission.permission_code))
                    )
                    .select_from(RoleSet)
                    .join(SetPermission, SetPermission.set_id == RoleSet.set_id)
                    .where(RoleSet.role_id.in_([role["id"] for role in roles]))
                    .group_by(RoleSet.role_id)
                )
            ).all()
        )
        for role in roles:
            role["permission_count"] = counts.get(role["id"], 0)
        return roles

    async def listing(
        self,
        kind: str,
        after: str = "",
        limit: int = 50,
        q: str = "",
        page: int | None = None,
        actor: str = "",
        action: str = "",
        since: str = "",
        until: str = "",
    ):
        self.access.need("audit.read" if kind == "audit" else "access.read")
        model: Any
        model, fields = {
            "employees": (Employee, ("id", "name", "active", "version")),
            "roles": (Role, ("id", "name", "version", "builtin_key")),
            "permission-sets": (PermissionSet, ("id", "name", "version")),
            "permissions": (Permission, ("code", "description")),
            "audit": (
                Audit,
                ("id", "actor_id", "action", "resource_id", "detail", "trace_id", "created_at"),
            ),
        }[kind]
        require(1 <= limit <= 100 and len(after) <= 100, 422, "invalid_pagination", "分页参数无效")
        key = model.id if hasattr(model, "id") else model.code
        query = select(model).where(key > after)
        q = q.strip()
        if q:
            if kind == "permissions":
                localized = [
                    code
                    for code, definition in DEFINITIONS.items()
                    if q.lower() in " ".join(definition).lower()
                ]
                query = query.where(
                    or_(
                        model.code.contains(q, autoescape=True),
                        model.description.contains(q, autoescape=True),
                        model.code.in_(localized),
                    )
                )
            elif kind == "audit":
                localized_actions = [
                    code for code, label in AUDIT_ACTIONS.items() if q.lower() in label.lower()
                ]
                query = query.where(
                    or_(
                        model.trace_id.contains(q, autoescape=True),
                        model.action.contains(q, autoescape=True),
                        model.action.in_(localized_actions),
                        model.resource_id.contains(q, autoescape=True),
                        Employee.name.contains(q, autoescape=True),
                    )
                ).join(Employee, Employee.id == model.actor_id)
            else:
                if kind == "employees":
                    login_match = (
                        select(IdentityLink.id)
                        .where(
                            IdentityLink.employee_id == Employee.id,
                            IdentityLink.issuer == self.issuer,
                            func.lower(IdentityLink.login_name).contains(
                                q.lower(), autoescape=True
                            ),
                        )
                        .exists()
                    )
                    query = query.where(
                        or_(
                            func.lower(model.name).contains(q.lower(), autoescape=True), login_match
                        )
                    )
                else:
                    query = query.where(func.lower(model.name).contains(q.lower(), autoescape=True))
        if kind == "audit":
            if actor:
                query = query.where(
                    Audit.actor_id.in_(
                        select(Employee.id).where(Employee.name.contains(actor, autoescape=True))
                    )
                )
            if action:
                query = query.where(Audit.action == action)
            if since:
                query = query.where(Audit.created_at >= datetime.fromisoformat(since))
            if until:
                query = query.where(Audit.created_at < datetime.fromisoformat(until))
        pagination = None
        order = (
            (Audit.created_at.desc(), Audit.id.desc())
            if kind == "audit" and page is not None
            else (key,)
        )
        if page is not None:
            rows, pagination = await numbered_rows(self.db, query.order_by(*order), page, limit)
        else:
            rows = (await self.db.scalars(query.order_by(*order).limit(limit + 1))).all()
        items = [view(row, fields) for row in rows[:limit]]
        if kind == "audit":
            await self._decorate_audit(items)
        await Inspection(self.access).counts(kind, items)
        if kind == "employees":
            await Inspection(self.access).employee_accounts(items, self.issuer)
        return {
            "items": items,
            "pagination": pagination,
            "has_more": pagination["page"] < pagination["pages"]
            if pagination
            else len(rows) > limit,
            "next": getattr(rows[limit - 1], key.key) if len(rows) > limit else None,
        }

    async def _decorate_audit(self, items: list[dict]):
        """单页内批量补全可读名称；未知编号原样保留，不猜测业务对象。"""
        if not items:
            return
        actor_ids = list({row["actor_id"] for row in items})
        actor_names = dict(
            (
                await self.db.execute(
                    select(Employee.id, Employee.name).where(Employee.id.in_(actor_ids))
                )
            ).all()
        )
        resource_ids = list({row["resource_id"] for row in items})
        resource_names: dict[str, str] = {}
        if resource_ids:
            for model, label in (
                (Employee, None),
                (Role, "角色"),
                (PermissionSet, "权限集"),
                (Project, "项目"),
            ):
                found = dict(
                    (
                        await self.db.execute(
                            select(model.id, model.name).where(model.id.in_(resource_ids))
                        )
                    ).all()
                )
                for key, value in found.items():
                    resource_names[key] = value if label is None else label + "：" + value
        if resource_ids:
            operations = (
                await self.db.execute(
                    select(IdentityOperation.id, Employee.name)
                    .outerjoin(Employee, Employee.id == IdentityOperation.employee_id)
                    .where(IdentityOperation.id.in_(resource_ids))
                )
            ).all()
            for identifier, name in operations:
                resource_names[identifier] = "账号操作：" + (name or "待开户人员")
        if resource_ids:
            sessions = (
                await self.db.execute(
                    select(ClientAuthorization.id, ClientAuthorization.device_name, Employee.name)
                    .join(Employee)
                    .where(ClientAuthorization.id.in_(resource_ids))
                )
            ).all()
            for identifier, device, name in sessions:
                resource_names[identifier] = (
                    "客户端授权：" + name + (" / " + device if device else "")
                )
        for row in items:
            row["actor_name"] = actor_names.get(row["actor_id"], row["actor_id"])
            row["action_name"] = describe_audit_action(row["action"])
            row["resource_name"] = resource_names.get(row["resource_id"], row["resource_id"])
            row["detail_summary"] = describe_audit_detail(row["action"], row["detail"])

    async def create(self, kind: str, body: dict):
        self.access.need("access.manage")
        obj: Employee | Role | PermissionSet
        assigned = None
        if kind == "employees":
            data = NewEmployee.model_validate(body)
            require(
                data.issuer == self.issuer, 422, "issuer_mismatch", "只能绑定本实例信任的身份来源"
            )
            obj = Employee(name=data.name)
            self.db.add(obj)
            (await self.db.flush())
            self.db.add(IdentityLink(employee_id=obj.id, issuer=data.issuer, subject=data.subject))
            assigned = await assign_role(self.db, obj.id, data.assign_default_role)
        else:
            named = Named.model_validate(body)
            model = Role if kind == "roles" else PermissionSet
            # 授权修订排他锁覆盖检查与插入，防止并发创建同名空配置。
            existing = await self.db.scalar(
                select(model.id).where(func.lower(func.trim(model.name)) == named.name.lower())
            )
            require(existing is None, 409, "constraint_conflict", "已有同名配置，请复用现有对象")
            obj = model(name=named.name)
            self.db.add(obj)
            (await self.db.flush())
        (
            await self.access.changed(
                obj.id, {"kind": kind, "operation": "create", "assigned_role": assigned}
            )
        )
        return {"id": obj.id, "version": obj.version, "assigned_role": assigned}

    async def update_employee(self, identifier: str, body: dict):
        if not self.access.allows("access.manage"):
            self.access.need("employee.update")
        data = EmployeeUpdate.model_validate(body)
        obj = await self.db.get(Employee, identifier, with_for_update=True)
        require(obj is not None, 404, "not_found", "员工不存在")
        require(obj.version == data.version, 409, "version_conflict", "员工已更新，请刷新后重试")
        if obj.active != data.active:
            self.access.need("access.manage")
            pending = obj.security_hold is not None
            managed = await self.db.scalar(
                select(IdentityLink.id).where(
                    IdentityLink.employee_id == obj.id, IdentityLink.managed.is_(True)
                )
            )
            require(
                not pending and managed is None,
                409,
                "constraint_conflict",
                "请通过账号详情完成启停；此人员存在受管账号或安全操作",
            )
        obj.name, obj.active = data.name, data.active
        obj.version += 1
        (await self.access.changed(obj.id, {"kind": "employees", "operation": "update"}))
        return {"id": obj.id, "version": obj.version}

    async def pending_identities(self):
        self.access.need("access.manage")
        linked = (
            select(IdentityLink.id)
            .where(
                IdentityLink.issuer == PendingIdentity.issuer,
                IdentityLink.subject == PendingIdentity.subject,
            )
            .exists()
        )
        rows = (
            await self.db.scalars(
                select(PendingIdentity)
                .where(
                    PendingIdentity.claimed_by.is_(None),
                    PendingIdentity.issuer == self.issuer,
                    ~linked,
                )
                .order_by(PendingIdentity.first_seen_at)
                .limit(50)
            )
        ).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "display_name": row.display_name,
                    "subject": row.subject,
                    "first_seen_at": row.first_seen_at,
                    "last_seen_at": row.last_seen_at,
                }
                for row in rows
            ]
        }

    async def claim_identity(self, pending_id: str, name: str, assign_default_role: bool = True):
        self.access.need("access.manage")
        pending = await self.db.get(PendingIdentity, pending_id, with_for_update=True)
        require(pending is not None, 404, "not_found", "待认领登录不存在或已被处理")
        require(
            pending.issuer == self.issuer, 422, "issuer_mismatch", "只能认领本实例信任的身份来源"
        )
        require(pending.claimed_by is None, 409, "identity_claimed", "该登录已被认领，请刷新后查看")
        require(
            await self.db.scalar(
                select(IdentityLink.id).where(
                    IdentityLink.issuer == pending.issuer,
                    IdentityLink.subject == pending.subject,
                )
            )
            is None,
            409,
            "constraint_conflict",
            "该登录已绑定其他人员，请刷新后核对",
        )
        obj = Employee(name=name)
        self.db.add(obj)
        (await self.db.flush())
        self.db.add(
            IdentityLink(employee_id=obj.id, issuer=pending.issuer, subject=pending.subject)
        )
        assigned = await assign_role(self.db, obj.id, assign_default_role)
        pending.claimed_by, pending.claimed_at = self.access.actor.id, func.now()
        (await self.db.flush())
        (
            await self.access.changed(
                obj.id, {"kind": "employees", "operation": "create", "assigned_role": assigned}
            )
        )
        self.access.audit(
            "access.identity.claim", pending.id, {"employee_id": obj.id, "assigned_role": assigned}
        )
        return {"id": obj.id, "version": obj.version, "assigned_role": assigned}

    async def replace(self, kind: str, identifier: str, relation: str, body: dict):
        self.access.need("access.manage")
        model: Any
        association: Any
        target: Any
        if kind == "employees" and relation == "roles":
            model, association, foreign, remote, target = (
                Employee,
                EmployeeRole,
                "employee_id",
                "role_id",
                Role,
            )
        elif kind == "roles" and relation == "permission-sets":
            model, association, foreign, remote, target = (
                Role,
                RoleSet,
                "role_id",
                "set_id",
                PermissionSet,
            )
        else:
            model, association, foreign, remote, target = (
                PermissionSet,
                SetPermission,
                "set_id",
                "permission_code",
                Permission,
            )
        data = (GrantUpdate if target is Permission else Links).model_validate(body)
        ids = data.permissions if isinstance(data, GrantUpdate) else data.ids
        require(len(ids) == len(set(ids)), 422, "duplicate_links", "关联项不能重复")
        obj = await self.db.get(model, identifier, with_for_update=True)
        require(obj is not None, 404, "not_found", "授权对象不存在")
        require(obj.version == data.version, 409, "version_conflict", "授权配置已更新，请刷新")
        for value in ids:
            require(
                (await self.db.get(target, value)) is not None, 422, "unknown_link", "关联项未登记"
            )
        (
            await self.db.execute(
                delete(association).where(getattr(association, foreign) == identifier)
            )
        )
        self.db.add_all([association(**{foreign: identifier, remote: value}) for value in ids])
        obj.version += 1
        (
            await self.access.changed(
                identifier, {"kind": kind, "relation": relation, "count": len(ids)}
            )
        )
        return {"id": obj.id, "version": obj.version, "ids": ids}

    async def associations(self, kind: str, identifier: str, relation: str):
        self.access.need("access.read")
        model: Any
        association: Any
        if kind == "employees" and relation == "roles":
            model, association, foreign, remote = Employee, EmployeeRole, "employee_id", "role_id"
        elif kind == "roles" and relation == "permission-sets":
            model, association, foreign, remote = Role, RoleSet, "role_id", "set_id"
        else:
            model, association, foreign, remote = (
                PermissionSet,
                SetPermission,
                "set_id",
                "permission_code",
            )
        obj = await self.db.get(model, identifier)
        require(obj is not None, 404, "not_found", "授权对象不存在")
        return {
            "id": identifier,
            "version": obj.version,
            "ids": sorted(
                (
                    await self.db.scalars(
                        select(getattr(association, remote)).where(
                            getattr(association, foreign) == identifier
                        )
                    )
                ).all()
            ),
        }
