"""临时模型探针复用测试留痕，不生成配置修订；请求标识防止重复计费。"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("model_connection_test", "revision", existing_type=sa.Integer(), nullable=True)
    op.add_column("model_connection_test", sa.Column("request_key", sa.String(100), nullable=True))
    op.create_index(
        "uq_model_test_actor_request",
        "model_connection_test",
        ["actor_id", "request_key"],
        unique=True,
    )


def downgrade():
    raise RuntimeError("临时测试留痕禁止自动删除，请前向修复")
