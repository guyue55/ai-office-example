"""任务内有界展示快照；不新增可重放执行历史或业务审批版本。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_run", sa.Column("progress", postgresql.JSONB(), nullable=True))


def downgrade():
    raise RuntimeError("任务过程留痕禁止自动删除，请前向修复")
