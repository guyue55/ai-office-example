"""登录会话的即时业务撤销与可审计管理；不保存令牌。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, insert

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "client_authorization",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=False),
        sa.Column("employee_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("clients", JSONB(), nullable=False),
        sa.Column("device_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("platform", sa.String(100), nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup", sa.String(20), nullable=False, server_default="not_requested"),
        sa.UniqueConstraint("issuer", "session_id", name="uq_client_authorization_session"),
        sa.CheckConstraint(
            "state IN ('active','disabled','revoked')", name="ck_client_authorization_state"
        ),
    )
    op.create_index(
        "ix_client_authorization_employee_created",
        "client_authorization",
        ["employee_id", "created_at", "id"],
    )
    op.create_index("ix_client_authorization_created", "client_authorization", ["created_at", "id"])
    table = sa.table("permission", sa.column("code"), sa.column("description"))
    for code, description in {
        "session.read.all": "查看全体员工的客户端授权会话",
        "session.manage.all": "停用、撤销及归档其他员工的授权会话",
    }.items():
        op.execute(
            insert(table)
            .values(code=code, description=description)
            .on_conflict_do_nothing(index_elements=["code"])
        )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM office.client_authorization)")):
        raise RuntimeError("会话授权已使用，禁止删除安全撤销记录或回退到忽略状态的版本")
    op.drop_table("client_authorization")
