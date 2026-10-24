"""修正待认领时间列约束，使既有数据库与非空输出契约一致。"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # 兼容旧迁移允许的空值；保留已有时间，只有缺失时才使用可追溯的数据库当前时间。
    op.execute(
        "UPDATE office.pending_identity "
        "SET first_seen_at = COALESCE(first_seen_at, last_seen_at, CURRENT_TIMESTAMP), "
        "last_seen_at = COALESCE(last_seen_at, first_seen_at, CURRENT_TIMESTAMP) "
        "WHERE first_seen_at IS NULL OR last_seen_at IS NULL"
    )
    for column in ("first_seen_at", "last_seen_at"):
        op.alter_column(
            "pending_identity", column, existing_type=sa.DateTime(timezone=True), nullable=False
        )


def downgrade():
    # 仅放宽约束，不删除登记或修改已有时间。
    for column in ("first_seen_at", "last_seen_at"):
        op.alter_column(
            "pending_identity", column, existing_type=sa.DateTime(timezone=True), nullable=True
        )
