"""兼容旧创建接口，追加员工范围的项目创建请求映射。"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_creation",
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("employee.id"), primary_key=True),
        sa.Column("idempotency_key", sa.String(100), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON office.project_creation FROM office_app")


def downgrade():
    raise RuntimeError("创建请求已可能被重试使用，禁止自动删除幂等映射；请前向修复")
