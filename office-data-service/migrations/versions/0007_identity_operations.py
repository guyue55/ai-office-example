"""可恢复的账号操作与安全撤销；不向存量员工自动增加身份权限。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, insert

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employee", sa.Column("security_hold", sa.String(36), nullable=True))
    op.add_column(
        "employee", sa.Column("tokens_after", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "identity_link",
        sa.Column("managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "identity_operation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("employee_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=True),
        sa.Column("remote_id", sa.String(100), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("step", sa.String(40), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("message", sa.String(240), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column("access_revision", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_identity_operation_key"),
    )
    op.create_index("ix_identity_operation_status", "identity_operation", ["status"])
    op.create_index("ix_identity_operation_lease_until", "identity_operation", ["lease_until"])
    table = sa.table("permission", sa.column("code"), sa.column("description"))
    for code, description in {
        "identity.read": "查看本企业可管理的登录资料与处理进度",
        "identity.create": "通过受控流程开通员工登录账号",
        "identity.update": "修改允许的账号资料与登录启停",
        "identity.credentials.manage": "在受控浏览器中准备或重置员工凭证",
        "identity.sessions.revoke": "结束员工已有登录并撤销旧访问",
    }.items():
        op.execute(
            insert(table)
            .values(code=code, description=description)
            .on_conflict_do_nothing(index_elements=["code"])
        )


def downgrade():
    # 旧验证器不能忽略已经使用的安全标记；保留数据并明确拒绝不安全降级。
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM office.identity_operation) OR EXISTS(SELECT 1 FROM office.employee WHERE security_hold IS NOT NULL OR tokens_after>0)"
        )
    ):
        raise RuntimeError("账号操作已使用，需保留兼容的安全守门版本，不允许删除撤销状态")
    op.drop_table("identity_operation")
    op.drop_column("identity_link", "managed")
    op.drop_column("employee", "tokens_after")
    op.drop_column("employee", "security_hold")
