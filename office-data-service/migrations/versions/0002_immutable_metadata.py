"""收紧运行角色对历史结构、权限目录和迁移标记的访问。"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON office.project_schema FROM office_app")
    op.execute("REVOKE ALL ON office.permission FROM office_app")
    op.execute("GRANT SELECT ON office.permission TO office_app")
    op.execute("REVOKE ALL ON office.alembic_version FROM office_app")


def downgrade():
    raise RuntimeError("权限回退须经明确审查，不自动放宽运行账号权限")
