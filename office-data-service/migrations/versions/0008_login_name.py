"""人员列表账号摘要与操作历史索引；不保存登录秘密。"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("identity_link", sa.Column("login_name", sa.String(100), nullable=True))
    op.create_index(
        "ix_identity_operation_employee_created",
        "identity_operation",
        ["employee_id", "created_at", "id"],
    )


def downgrade():
    op.drop_index("ix_identity_operation_employee_created", table_name="identity_operation")
    op.drop_column("identity_link", "login_name")
