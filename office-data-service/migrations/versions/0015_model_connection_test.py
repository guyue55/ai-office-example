"""连接测试元数据；无模型输出或秘密，重启不自动重试。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_connection_test",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "revision",
            sa.Integer(),
            sa.ForeignKey("model_configuration_revision.revision"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("api", sa.String(40), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_model_connection_test_created", "model_connection_test", ["created_at", "id"]
    )
    op.execute("REVOKE DELETE, TRUNCATE ON office.model_connection_test FROM office_app")


def downgrade():
    raise RuntimeError("测试留痕禁止自动删除，请前向修复")
