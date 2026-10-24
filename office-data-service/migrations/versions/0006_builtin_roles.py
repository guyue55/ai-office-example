"""一次性登记内置职责；不修改存量人员或在启动时补权。"""

from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("role", sa.Column("builtin_key", sa.String(50), nullable=True))
    op.create_unique_constraint("uq_role_builtin_key", "role", ["builtin_key"])
    connection = op.get_bind()
    # 迁移快照固定初始权限，不导入未来可能变化的应用目录。
    definitions = (
        (
            "employee",
            "普通员工",
            ("project.read", "record.create", "record.read.own", "record.manage.own"),
        ),
        ("access-admin", "人员与授权管理员", ("access.read", "access.manage")),
    )
    descriptions = {
        "project.read": "查看普通项目元数据和字段定义",
        "record.create": "向项目上报候选数据",
        "record.read.own": "查看本人上报数据",
        "record.manage.own": "修改或归档本人上报数据",
        "access.read": "查看员工、角色、权限集与授权审计",
        "access.manage": "管理员工身份、角色和权限集",
    }
    for key, name, permissions in definitions:
        role_id = str(uuid5(NAMESPACE_URL, "office:builtin-role:" + key))
        set_id = str(uuid5(NAMESPACE_URL, "office:builtin-permission-set:" + key))
        # 降级保留了角色及其定制关系；重新升级只恢复标记，不重置授权。
        if connection.scalar(sa.text("SELECT 1 FROM office.role WHERE id=:id"), {"id": role_id}):
            connection.execute(
                sa.text("UPDATE office.role SET builtin_key=:key WHERE id=:id"),
                {"key": key, "id": role_id},
            )
            continue
        names = {}
        for table, initial in (("role", name), ("permission_set", name + "基础权限集")):
            candidate, suffix = initial, 0
            while connection.scalar(
                sa.text(f"SELECT 1 FROM office.{table} WHERE lower(trim(name))=lower(:name)"),
                {"name": candidate},
            ):
                suffix += 1
                candidate = initial + f"（内置{suffix}）"
            names[table] = candidate
        connection.execute(
            sa.text(
                "INSERT INTO office.role (id,name,version,builtin_key) VALUES (:id,:name,1,:key)"
            ),
            {"id": role_id, "name": names["role"], "key": key},
        )
        connection.execute(
            sa.text("INSERT INTO office.permission_set (id,name,version) VALUES (:id,:name,1)"),
            {"id": set_id, "name": names["permission_set"]},
        )
        connection.execute(
            sa.text("INSERT INTO office.role_permission_set (role_id,set_id) VALUES (:role,:set)"),
            {"role": role_id, "set": set_id},
        )
        for code in permissions:
            connection.execute(
                sa.text(
                    "INSERT INTO office.permission (code,description) VALUES (:code,:description) ON CONFLICT (code) DO NOTHING"
                ),
                {"code": code, "description": descriptions[code]},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO office.permission_set_permission (set_id,permission_code) VALUES (:set,:code)"
                ),
                {"set": set_id, "code": code},
            )


def downgrade():
    # 保留已被使用的职责和授权，避免回退造成失权。
    op.drop_constraint("uq_role_builtin_key", "role", type_="unique")
    op.drop_column("role", "builtin_key")
