"""迁移使用 owner 身份，与运行服务账号分离。"""

from alembic import context
from app.infrastructure.config import Settings
from app.infrastructure.database import create_database_engine
from app.infrastructure.models import Base

config = context.config
settings = Settings.load()
if settings.database_user != "office_owner":
    raise RuntimeError("迁移必须使用独立 owner 身份")
engine = create_database_engine(settings)
with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        version_table_schema="office",
        include_object=lambda obj, name, type_, reflected, compare_to: name != "alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()
