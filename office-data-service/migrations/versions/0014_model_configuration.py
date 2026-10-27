"""托管模型配置修订与独立权限；迁移不读取密钥、不启用模型。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_run", sa.Column("model_revision", sa.Integer(), nullable=True))
    op.create_table(
        "model_configuration",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=True),
        sa.Column("active_revision", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
    )
    op.create_table(
        "model_configuration_revision",
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("secret_ciphertext", sa.String(12000), nullable=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute("INSERT INTO office.model_configuration (id,version,source) VALUES (1,0,'external')")
    op.execute("REVOKE DELETE, TRUNCATE ON office.model_configuration FROM office_app")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON office.model_configuration_revision FROM office_app"
    )
    for code, description in {
        "agent.config.read": "查看脱敏模型配置与运行状态",
        "agent.config.manage": "保存模型配置草稿及替换密钥",
        "agent.config.apply": "启用或停用服务端模型配置",
        "agent.config.test": "明确确认费用后测试模型连接",
    }.items():
        op.get_bind().execute(
            sa.text(
                "INSERT INTO office.permission (code,description) VALUES (:code,:description) ON CONFLICT DO NOTHING"
            ),
            {"code": code, "description": description},
        )
        op.get_bind().execute(
            sa.text(
                "INSERT INTO office.permission_set_permission (set_id,permission_code) SELECT DISTINCT set_id,:code FROM office.permission_set_permission WHERE permission_code='access.manage' ON CONFLICT DO NOTHING"
            ),
            {"code": code},
        )
    op.execute(
        "UPDATE office.permission_set SET version=version+1 WHERE id IN (SELECT set_id FROM office.permission_set_permission WHERE permission_code='agent.config.manage')"
    )
    op.execute("UPDATE office.access_revision SET revision=revision+1 WHERE id=1")


def downgrade():
    raise RuntimeError("托管模型已有配置和审计，禁止自动删除；请采用前向修复")
