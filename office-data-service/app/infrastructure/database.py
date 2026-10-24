"""数据库引擎与会话工厂；秘密只在显式构造引擎时读取，不进入日志。"""

from sqlalchemy import URL, create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.infrastructure.config import Settings


def create_database_engine(settings: Settings, *, asynchronous=False):
    secret = settings.database_password_file
    if secret.stat().st_mode & 0o077:
        raise RuntimeError("数据库凭证文件必须仅当前用户可读写")
    url = URL.create(
        "postgresql+psycopg",
        username=settings.database_user,
        password=secret.read_text().strip(),
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
    )
    create = create_async_engine if asynchronous else create_engine
    return create(
        url,
        pool_pre_ping=True,
        pool_size=settings.database_runtime.pool_size,
        max_overflow=settings.database_runtime.max_overflow,
        pool_timeout=settings.database_runtime.pool_timeout,
        hide_parameters=True,
        connect_args={
            "options": (
                "-c search_path=office "
                f"-c statement_timeout={settings.database_runtime.statement_timeout_ms} "
                f"-c lock_timeout={settings.database_runtime.lock_timeout_ms}"
            )
        },
    )


def sessions(settings: Settings):
    return sessionmaker(create_database_engine(settings), expire_on_commit=False)


def async_sessions(settings: Settings):
    return async_sessionmaker(
        create_database_engine(settings, asynchronous=True), expire_on_commit=False
    )
