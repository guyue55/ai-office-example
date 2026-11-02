"""新增本人会话及任务关联；旧任务保持独立，不回放审批或自动启动。"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_conversation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_agent_conversation_owner_updated",
        "agent_conversation",
        ["owner_id", "updated_at", "id"],
    )
    op.add_column(
        "agent_run",
        sa.Column(
            "conversation_id", sa.String(36), sa.ForeignKey("agent_conversation.id"), nullable=True
        ),
    )
    op.add_column(
        "agent_run", sa.Column("context_page", sa.String(40), server_default="", nullable=False)
    )
    op.add_column("agent_run", sa.Column("access_fingerprint", sa.String(64), nullable=True))
    op.create_index("ix_agent_run_conversation_id", "agent_run", ["conversation_id"])
    op.execute("REVOKE DELETE, TRUNCATE ON office.agent_conversation FROM office_app")


def downgrade():
    raise RuntimeError("会话可能已有任务与审批证据，禁止自动删除；请采用前向修复")
