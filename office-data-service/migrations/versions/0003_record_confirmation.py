"""增加只追加的确认快照；既有候选不自动确认或扩大权限。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "record", sa.Column("current_revision", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("record", sa.Column("correction_reason", sa.String(1000), nullable=True))
    op.create_table(
        "record_confirmation",
        sa.Column("record_id", sa.String(36), sa.ForeignKey("record.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("schema_revision", sa.Integer(), nullable=False),
        sa.Column("values", pg.JSONB(), nullable=False),
        sa.Column("confirmed_by", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column(
            "confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reason", sa.String(1000), nullable=True),
        sa.CheckConstraint("revision >= 1", name="ck_confirmation_revision"),
    )
    op.execute("REVOKE ALL ON office.record_confirmation FROM office_app")
    op.execute("GRANT SELECT, INSERT ON office.record_confirmation TO office_app")
    op.execute(
        "INSERT INTO office.permission (code, description) VALUES ('record.confirm', '确认当前读取范围内的候选数据')"
    )


def downgrade():
    raise RuntimeError("确认历史不得自动删除；回退需保留快照并单独审查")
