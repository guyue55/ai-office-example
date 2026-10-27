"""临时PG上验证身份迁址，不连接既有企业库。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.infrastructure.database import sessions
from app.infrastructure.models import Audit, ClientAuthorization, IdentityLink, PendingIdentity
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from test_query_postgres import isolated_query_db  # noqa: F401
from test_relocation import NEW, OLD, module

pytestmark = pytest.mark.live


def test_relocation_preserves_people_and_history(isolated_query_db, monkeypatch):  # noqa: F811
    tool = module(monkeypatch, "relocation_phases")
    factory = sessions(isolated_query_db)
    engine = factory.kw["bind"]
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            with Session(connection, join_transaction_mode="create_savepoint") as db:
                db.execute(update(IdentityLink).values(issuer=OLD + "/identity/realms/office"))
                actor = db.scalar(select(IdentityLink.employee_id).limit(1))
                now = datetime.now(UTC)
                row = ClientAuthorization(
                    issuer=OLD + "/identity/realms/office",
                    session_id="old-session",
                    employee_id=actor,
                    clients=["office-cli"],
                    device_name="隔离设备",
                    platform="test",
                    state="active",
                    authenticated_at=now,
                    token_expires_at=now + timedelta(minutes=10),
                )
                pending = PendingIdentity(
                    issuer=OLD + "/identity/realms/office",
                    subject="pending-one",
                    display_name="待核实合成人员",
                )
                db.add_all([row, pending])
                db.flush()
                before = tool.database_state(db, OLD, NEW, str(uuid4()))
                tool.database_state(db, OLD, NEW, str(uuid4()), apply=True)
                db.expire_all()
                assert db.get(ClientAuthorization, row.id).state == "revoked"
                assert db.get(ClientAuthorization, row.id).issuer == OLD + "/identity/realms/office"
                assert db.get(PendingIdentity, pending.id).issuer == NEW + "/identity/realms/office"
                assert (
                    db.scalar(
                        select(IdentityLink.employee_id).where(
                            IdentityLink.issuer == NEW + "/identity/realms/office"
                        )
                    )
                    == actor
                )
                assert (
                    db.scalar(select(Audit.action).where(Audit.action == "instance.relocated"))
                    == "instance.relocated"
                )
                # 原链接标识和人员／主体组合保留，只有受信任来源地址改变。
                assert before["counts"]["identity_link"] == db.query(IdentityLink).count()
                with pytest.raises(RuntimeError):
                    tool.database_state(db, OLD, NEW, str(uuid4()), apply=True)
            transaction.rollback()
    finally:
        engine.dispose()
