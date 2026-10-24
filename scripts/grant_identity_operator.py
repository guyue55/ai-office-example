"""显式为本机已存在的开发operator关联账号管理员职责；不在启动时运行。"""

import argparse
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.infrastructure.config import Settings
from app.infrastructure.database import sessions
from app.infrastructure.models import (
    AccessRevision,
    Audit,
    Employee,
    EmployeeRole,
    IdentityLink,
    PermissionSet,
    Role,
    RoleSet,
    SetPermission,
)
from dev_stack import ROOT
from sqlalchemy import select


def main():
    parser = argparse.ArgumentParser(
        description="显式配置本机开发管理员职责；不在启动时自动升级权限"
    )
    parser.add_argument("--client-authorizations", action="store_true")
    args = parser.parse_args()
    key = "session-admin" if args.client_authorizations else "identity-admin"
    name = "客户端授权管理员" if args.client_authorizations else "账号管理员"
    permissions = (
        ("session.read.all", "session.manage.all")
        if args.client_authorizations
        else (
            "access.read",
            "access.manage",
            "identity.read",
            "identity.create",
            "identity.update",
            "identity.credentials.manage",
            "identity.sessions.revoke",
        )
    )
    settings = Settings.load(str(ROOT / "runtime/foundation/owner.json"))
    if settings.issuer != "http://127.0.0.1:8081/realms/office":
        raise RuntimeError("此脚本仅限本机开发realm")
    subject = json.loads((ROOT / "runtime/foundation/operator.json").read_text())["subject"]
    factory = sessions(settings)
    try:
        with factory.begin() as db:
            revision = db.scalar(
                select(AccessRevision).where(AccessRevision.id == 1).with_for_update()
            )
            employee = db.scalar(
                select(Employee)
                .join(IdentityLink)
                .where(
                    IdentityLink.issuer == settings.issuer,
                    IdentityLink.subject == subject,
                    Employee.active,
                )
            )
            if employee is None:
                raise RuntimeError("开发operator业务身份未开通")
            grants = set(
                db.scalars(
                    select(SetPermission.permission_code)
                    .join(RoleSet, RoleSet.set_id == SetPermission.set_id)
                    .join(EmployeeRole, EmployeeRole.role_id == RoleSet.role_id)
                    .where(EmployeeRole.employee_id == employee.id)
                )
            )
            if not {"access.read", "access.manage"} <= grants:
                raise RuntimeError("选定人员不具备既有业务管理职责，不自动升级")
            role = db.scalar(select(Role).where(Role.builtin_key == key))
            if role is None:
                role = Role(
                    id=str(uuid5(NAMESPACE_URL, "office:builtin-role:" + key)),
                    name=name,
                    builtin_key=key,
                )
                bundle = PermissionSet(
                    id=str(uuid5(NAMESPACE_URL, "office:builtin-set:" + key)),
                    name=name + "权限集",
                )
                db.add_all([role, bundle])
                db.flush()
                db.add(RoleSet(role_id=role.id, set_id=bundle.id))
                db.add_all(
                    [SetPermission(set_id=bundle.id, permission_code=p) for p in permissions]
                )
            if db.get(EmployeeRole, (employee.id, role.id)) is None:
                db.add(EmployeeRole(employee_id=employee.id, role_id=role.id))
                employee.version += 1
                revision.revision += 1
                db.add(
                    Audit(
                        actor_id=employee.id,
                        action="access.changed",
                        resource_id=employee.id,
                        detail={
                            "kind": "employees",
                            "reason": "本机显式配置" + name,
                            "revision": revision.revision,
                        },
                        trace_id=str(uuid4()),
                    )
                )
    finally:
        factory.kw["bind"].dispose()
    print("已显式关联本机operator的" + name + "职责；其他员工授权未变更。")


if __name__ == "__main__":
    main()
