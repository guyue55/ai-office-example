"""已有0013数据库升级：保留数据、一次性授权、运行账号无法改写历史修订。"""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from app.infrastructure.database import sessions
from app.infrastructure.models import Permission, Project, SetPermission
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from test_query_postgres import isolated_query_db  # noqa: F401

pytestmark = pytest.mark.live


@pytest.mark.parametrize("isolated_query_db", ["0013"], indirect=True)
def test_existing_database_upgrade_preserves_data_and_seeds_once(isolated_query_db):  # noqa: F811
    settings = isolated_query_db
    folder = settings.database_password_file.parent
    owner = settings.model_copy(
        update={
            "database_user": "office_owner",
            "database_password_file": folder / "owner_password",
        }
    )
    factory = sessions(owner)
    target = uuid4().hex
    try:
        with factory.begin() as db:
            # 模拟旧版本权限目录：当前bootstrap来自新源码，先移除其额外项。
            db.execute(
                text(
                    "DELETE FROM office.permission_set_permission WHERE permission_code LIKE 'agent.config.%'"
                )
            )
            db.execute(text("DELETE FROM office.permission WHERE code LIKE 'agent.config.%'"))
            versions = dict(db.execute(text("SELECT id,version FROM office.permission_set")).all())
            db.add(Project(id=target, name="升级保留测试"))
        command = [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "office-data-service/alembic.ini",
            "upgrade",
            "head",
        ]
        for _ in range(2):
            result = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "OFFICE_CONFIG": str(folder / "owner.json")},
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert result.returncode == 0, "升级失败；不回显配置或SQL参数"
            with factory() as db:
                grants = list(
                    db.scalars(
                        select(Permission.code).where(Permission.code.like("agent.config.%"))
                    )
                )
                assert len(grants) == 4
                for set_id, before in versions.items():
                    count = db.scalar(
                        text(
                            "SELECT count(*) FROM office.permission_set_permission WHERE set_id=:id AND permission_code LIKE 'agent.config.%'"
                        ),
                        {"id": set_id},
                    )
                    if db.scalar(
                        select(SetPermission).where(
                            SetPermission.set_id == set_id,
                            SetPermission.permission_code == "access.manage",
                        )
                    ):
                        assert count == 4
                        assert (
                            db.scalar(
                                text("SELECT version FROM office.permission_set WHERE id=:id"),
                                {"id": set_id},
                            )
                            == before + 1
                        )
                assert (
                    db.scalar(text("SELECT name FROM office.project WHERE id=:id"), {"id": target})
                    == "升级保留测试"
                )
        runtime = sessions(settings)
        try:
            with runtime.begin() as db:
                assert (
                    db.scalar(text("SELECT version FROM office.model_configuration WHERE id=1"))
                    == 0
                )
            with pytest.raises(ProgrammingError), runtime.begin() as db:
                db.execute(
                    text(
                        "UPDATE office.model_configuration_revision SET config=CAST(:value AS jsonb)"
                    ),
                    {"value": json.dumps({})},
                )
            with runtime.begin() as db:
                assert db.scalar(text("SELECT count(*) FROM office.model_connection_test")) == 0
            with pytest.raises(ProgrammingError), runtime.begin() as db:
                db.execute(text("DELETE FROM office.model_connection_test"))
        finally:
            runtime.kw["bind"].dispose()
    finally:
        factory.kw["bind"].dispose()
