"""拆分审计读取与普通姓名维护；一次性保留原有能力，不在启动时补权。"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for code, description, old in (
        ("audit.read", "查看全企业业务与安全审计", "access.read"),
        ("employee.update", "仅修改业务显示姓名，不含启停和授权", "access.manage"),
    ):
        connection.execute(
            sa.text(
                "INSERT INTO office.permission (code,description) VALUES (:code,:description) ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "description": description},
        )
        # 只迁移已有关系；未来再授予access.read不会隐式获得审计权限。
        connection.execute(
            sa.text(
                "INSERT INTO office.permission_set_permission (set_id,permission_code) SELECT set_id,:code FROM office.permission_set_permission WHERE permission_code=:old ON CONFLICT DO NOTHING"
            ),
            {"code": code, "old": old},
        )
        connection.execute(
            sa.text(
                "UPDATE office.permission_set SET version=version+1 WHERE id IN (SELECT set_id FROM office.permission_set_permission WHERE permission_code=:code)"
            ),
            {"code": code},
        )
    connection.execute(
        sa.text(
            "UPDATE office.permission SET description='查看员工、角色和权限集' WHERE code='access.read'"
        )
    )
    connection.execute(sa.text("UPDATE office.access_revision SET revision=revision+1 WHERE id=1"))


def downgrade():
    raise RuntimeError("审计权限已独立管理，禁止自动降级恢复隐式授权；请采用前向修复")
