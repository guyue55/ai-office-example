"""初始化基础身份、授权与项目数据表。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def entity():
    return sa.Column("id", sa.String(36), primary_key=True)


def upgrade():
    # 迁移固定声明，避免后续模型变化影响旧迁移的重放。
    op.create_table(
        "employee",
        entity(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "identity_link",
        entity(),
        sa.Column("employee_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.UniqueConstraint("issuer", "subject"),
    )
    op.create_index("ix_identity_link_employee_id", "identity_link", ["employee_id"])
    for table in ("role", "permission_set"):
        op.create_table(
            table,
            entity(),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("version", sa.Integer(), nullable=False),
        )
    op.create_table(
        "permission",
        sa.Column("code", sa.String(100), primary_key=True),
        sa.Column("description", sa.String(200), nullable=False),
    )
    for table, left, right, left_ref, right_ref in (
        ("employee_role", "employee_id", "role_id", "employee.id", "role.id"),
        ("role_permission_set", "role_id", "set_id", "role.id", "permission_set.id"),
        (
            "permission_set_permission",
            "set_id",
            "permission_code",
            "permission_set.id",
            "permission.code",
        ),
    ):
        op.create_table(
            table,
            sa.Column(left, sa.String(36), sa.ForeignKey(left_ref), primary_key=True),
            sa.Column(
                right,
                sa.String(100 if right == "permission_code" else 36),
                sa.ForeignKey(right_ref),
                primary_key=True,
            ),
        )
    op.create_table(
        "access_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1"),
    )
    op.execute("INSERT INTO access_revision (id, revision) VALUES (1, 1)")
    op.create_table(
        "audit_event",
        entity(),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=False),
        sa.Column("detail", pg.JSONB(), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_audit_event_created_at", "audit_event", ["created_at"])
    op.create_table(
        "project",
        entity(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "project_schema",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("project.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("fields", pg.JSONB(), nullable=False),
    )
    op.create_table(
        "record",
        entity(),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("schema_revision", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("values", pg.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            ["project_id", "schema_revision"],
            ["project_schema.project_id", "project_schema.revision"],
        ),
    )
    for column in ("project_id", "owner_id"):
        op.create_index("ix_record_" + column, "record", [column])
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON office.audit_event FROM office_app")
    op.execute("REVOKE INSERT, DELETE ON office.permission FROM office_app")
    op.execute("REVOKE DELETE, INSERT ON office.access_revision FROM office_app")


def downgrade():
    raise RuntimeError("基础迁移不自动删除数据；请通过经审批的恢复方案回退")
