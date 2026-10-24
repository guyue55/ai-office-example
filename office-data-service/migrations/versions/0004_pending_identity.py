"""新增待认领登录登记；首次访问留痕不改变未开通员工的拒绝语义。"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pending_identity",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("claimed_by", sa.String(36), sa.ForeignKey("employee.id"), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("issuer", "subject", name="uq_pending_identity_subject"),
    )
    op.execute("REVOKE ALL ON office.pending_identity FROM office_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON office.pending_identity TO office_app")


def downgrade():
    raise RuntimeError("访问留痕不得自动删除；回退需先归档并单独审查")
