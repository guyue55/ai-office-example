"""已有专用PG18连接预检与幂等结构初始化；不创建数据库、不接管业务表。"""

import re
from pathlib import Path

import psycopg
from psycopg import sql


def validate_external(value):
    keys = {
        "host",
        "port",
        "name",
        "owner",
        "user",
        "identity_name",
        "identity_user",
        "owner_password_file",
        "app_password_file",
        "identity_password_file",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("外部数据库配置不完整")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", value["host"]):
        raise ValueError("数据库主机格式无效")
    if type(value["port"]) is not int or not 1 <= value["port"] <= 65535:
        raise ValueError("数据库端口无效")
    for key in ("name", "owner", "user", "identity_name", "identity_user"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value[key]):
            raise ValueError("数据库或账号名称格式无效")
    if len({value["owner"], value["user"], value["identity_user"]}) != 3:
        raise ValueError("初始化、运行及身份数据库账号必须分离")
    if value["owner"] != "office_owner":
        raise ValueError("当前迁移基线要求初始化账号名为office_owner")
    if value["user"] != "office_app":
        raise ValueError("当前迁移基线要求运行账号名为office_app")
    if value["name"] == value["identity_name"]:
        raise ValueError("业务库与身份库必须分离")
    for key in ("owner_password_file", "app_password_file", "identity_password_file"):
        path = value[key]
        if (
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or any(c in path for c in "\r\n:")
        ):
            raise ValueError("数据库密码须引用单行绝对文件路径")


def connect(root, cfg, kind):
    identity = kind == "identity"
    password = {
        "owner": "maintenance/secrets/owner_password",
        "app": "service/secrets/app_password",
        "identity": "identity/secrets/database_password",
    }[kind]
    return psycopg.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["identity_name"] if identity else cfg["name"],
        user=cfg["identity_user"] if identity else cfg["owner"] if kind == "owner" else cfg["user"],
        password=(root / password).read_text().strip(),
        connect_timeout=10,
        options="-c statement_timeout=30000 -c lock_timeout=5000",
    )


def initialize_external(root, cfg):
    validate_external(cfg)
    # 全部账号先连接检查，再做任何DDL，禁止失败后悄悄创建替代数据库。
    for kind in ("owner", "app", "identity"):
        with connect(root, cfg, kind) as db:
            version = int(db.execute("SHOW server_version_num").fetchone()[0])
            if not 180000 <= version < 190000:
                raise ValueError("部署基线要求PG18")
            privileged = db.execute(
                "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()[0]
            if privileged:
                raise ValueError("外部数据库账号不得具有集群管理权限")
            if (
                kind == "app"
                and db.execute(
                    "SELECT pg_has_role(current_user,%s,'USAGE') OR has_database_privilege(current_database(),'CREATE') OR has_schema_privilege('public','CREATE')",
                    (cfg["owner"],),
                ).fetchone()[0]
            ):
                raise ValueError("运行账号不得拥有初始化账号权限或建表权限")
            if (
                kind in ("owner", "identity")
                and not db.execute(
                    "SELECT pg_get_userbyid(datdba)=current_user FROM pg_database WHERE datname=current_database()"
                ).fetchone()[0]
            ):
                raise ValueError("初始化和身份账号须拥有对应专用数据库")
    with connect(root, cfg, "owner") as db:
        db.execute("SELECT pg_advisory_xact_lock(735091841)")
        owner = db.execute(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='office'"
        ).fetchone()
        if owner and owner[0] != cfg["owner"]:
            raise ValueError("已有office结构不归初始化账号所有")
        # 只接受空库或本系统迁移库，不向不明已有结构执行迁移。
        tables = db.execute(
            "SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') AND table_type='BASE TABLE'"
        ).fetchall()
        if tables and (
            ("office", "alembic_version") not in tables
            or any(schema != "office" for schema, _ in tables)
        ):
            raise ValueError("外部业务库含未知数据，拒绝接管")
        db.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        db.execute("CREATE SCHEMA IF NOT EXISTS office")
        db.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(cfg["name"]), sql.Identifier(cfg["user"])
            )
        )
        db.execute(
            sql.SQL("GRANT USAGE ON SCHEMA office TO {}").format(sql.Identifier(cfg["user"]))
        )
        db.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA office GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}"
            ).format(sql.Identifier(cfg["user"]))
        )


def validate_snapshot(snapshot, *, expected_head, expected_tables, expected_permissions):
    """校验必要结构与基础数据；允许未来扩展的额外表和权限，不自动修复或补写。"""
    if snapshot["heads"] != [expected_head] or not expected_head:
        raise ValueError("数据库迁移版本与安装包不一致，请检查迁移阶段")
    if not set(expected_tables) <= set(snapshot["tables"]):
        raise ValueError("数据库缺少必要表，请检查迁移阶段")
    if not set(expected_permissions) <= set(snapshot["permissions"]):
        raise ValueError("数据库缺少必要权限定义，请检查初始化阶段")
    if snapshot["employees"] < 1 or snapshot["revision"] is None:
        raise ValueError("首管理员或基础权限修订尚未初始化")
    if any(snapshot["forbidden_privileges"]):
        raise ValueError("运行账号具备不应拥有的表权限，请检查权限迁移")
    return {
        "migration_head": expected_head,
        "required_tables_verified": len(expected_tables),
        "required_permissions_verified": len(expected_permissions),
        "runtime_privileges_verified": True,
    }


def verify_database(root, *, migrations="/opt/office/migrations"):
    from alembic.script import ScriptDirectory
    from app.infrastructure.config import Settings
    from app.infrastructure.database import sessions
    from app.infrastructure.models import Base
    from app.modules.access.catalog import PERMISSIONS
    from sqlalchemy import inspect, text

    cfg = Settings.load(root / "maintenance/owner.json")
    if cfg.database_user != "office_owner":
        raise ValueError("部署结构检查须使用初始化账号")
    expected_head = ScriptDirectory(migrations).get_current_head()
    factory = sessions(cfg)
    try:
        with factory.begin() as db:
            snapshot = {
                "heads": list(db.scalars(text("SELECT version_num FROM office.alembic_version"))),
                "tables": inspect(db.connection()).get_table_names(schema="office"),
                "permissions": list(db.scalars(text("SELECT code FROM office.permission"))),
                "employees": db.scalar(text("SELECT count(*) FROM office.employee")),
                "revision": db.scalar(
                    text("SELECT revision FROM office.access_revision WHERE id=1")
                ),
                "forbidden_privileges": [
                    db.scalar(
                        text("SELECT has_table_privilege('office_app',:table,:privilege)"),
                        {"table": "office." + table, "privilege": privilege},
                    )
                    for table, privilege in (
                        ("alembic_version", "SELECT"),
                        ("audit_event", "DELETE"),
                        ("audit_event", "UPDATE"),
                        ("permission", "INSERT"),
                        ("project_schema", "UPDATE"),
                        ("record_confirmation", "UPDATE"),
                    )
                ],
            }
            return validate_snapshot(
                snapshot,
                expected_head=expected_head,
                expected_tables=[table.name for table in Base.metadata.tables.values()],
                expected_permissions=PERMISSIONS,
            )
    finally:
        factory.kw["bind"].dispose()
