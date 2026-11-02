"""新增可选智能助手权限和任务记录；不运行模型、不重放历史操作。"""

from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("prompt", sa.String(16000), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("pending", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("write_inflight", sa.Boolean(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_agent_run_request"),
        sa.CheckConstraint(
            "status IN ('queued','running','awaiting_confirmation','completed','failed',"
            "'cancelled','interrupted','outcome_unknown')",
            name="ck_agent_run_status",
        ),
    )
    op.create_index("ix_agent_run_owner_created", "agent_run", ["owner_id", "created_at", "id"])
    op.create_index("ix_agent_run_status", "agent_run", ["status"])
    op.execute("REVOKE DELETE, TRUNCATE ON office.agent_run FROM office_app")
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO office.permission (code,description) VALUES "
            "('agent.run','使用已启用的智能助手，仍仅限本人真实业务权限') ON CONFLICT DO NOTHING"
        )
    )
    # 一次性把新基础能力加入内置员工权限集及既有管理权限集；不在启动／登录时补权。
    employee_set = str(uuid5(NAMESPACE_URL, "office:builtin-permission-set:employee"))
    connection.execute(
        sa.text(
            "INSERT INTO office.permission_set_permission (set_id,permission_code) "
            "SELECT DISTINCT p.id,'agent.run' FROM office.permission_set p WHERE p.id=:employee_set "
            "OR EXISTS (SELECT 1 FROM office.permission_set_permission s WHERE s.set_id=p.id "
            "AND s.permission_code='access.manage') ON CONFLICT DO NOTHING"
        ),
        {"employee_set": employee_set},
    )
    connection.execute(
        sa.text(
            "UPDATE office.permission_set SET version=version+1 WHERE id IN "
            "(SELECT set_id FROM office.permission_set_permission WHERE permission_code='agent.run')"
        )
    )
    connection.execute(sa.text("UPDATE office.access_revision SET revision=revision+1 WHERE id=1"))


def downgrade():
    raise RuntimeError("Agent任务可能已有业务回执，禁止自动删除；请采用前向修复")
