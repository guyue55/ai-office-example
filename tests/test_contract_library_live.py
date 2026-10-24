"""新源码进程内HTTP与真实独立PG；复用浏览器授权，不清库。"""

from uuid import uuid4

import pytest
from app.infrastructure.database import sessions
from app.infrastructure.models import Project
from app.main import create_app
from fastapi.testclient import TestClient
from office_contracts import ProtocolError
from sqlalchemy import select


@pytest.fixture
def v2(live):
    app = create_app(live["settings"])
    with TestClient(app) as client:
        yield client, app, live


def request(client, actor, method, path, **kwargs):
    return client.request(
        method, path, headers={"Authorization": "Bearer " + actor.oauth.access_token()}, **kwargs
    )


def test_v2_real_pg_replay_and_permission(v2, project):
    client, _app, live = v2
    employee = live["employee"]
    payload = {
        "project_id": project["id"],
        "schema_revision": 1,
        "values": {"amount": "5.00"},
        "idempotency_key": uuid4().hex,
    }
    created = request(client, employee, "POST", "/api/v2/records", json=payload)
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["code"] == 0 and first["data"]["values"]["amount"] == "5.00"
    assert created.headers["x-trace-id"] == first["meta"]["trace_id"]
    replay = request(client, employee, "POST", "/api/v2/records", json=payload)
    assert replay.status_code == 200 and replay.json()["meta"]["replayed"] is True
    assert replay.json()["data"]["id"] == first["data"]["id"]
    denied = request(client, employee, "POST", "/api/v2/projects", json={"name": "不能创建"})
    assert denied.status_code == 403 and denied.json()["code"] == 403001
    old = request(client, employee, "GET", "/api/v2/projects")
    assert old.json()["code"] == 0 and "items" in old.json()["data"]


def test_preparation_failure_rolls_back_real_pg(v2, monkeypatch):
    client, app, live = v2
    application = app.state.application
    original = application.call
    marker = "回滚验证-" + uuid4().hex

    async def rejecting(principal, operation, payload, trace_id, *, presenter=None):
        def fail(_result, **kwargs):
            raise ProtocolError("合成输出准备失败")

        return await original(principal, operation, payload, trace_id, presenter=fail)

    monkeypatch.setattr(application, "call", rejecting)
    result = request(client, live["operator"], "POST", "/api/v2/projects", json={"name": marker})
    assert result.status_code == 500 and result.json()["code"] == 500002
    with sessions(live["settings"])() as db:
        assert db.scalar(select(Project.id).where(Project.name == marker)) is None


def test_commit_failure_does_not_publish_prepared_success(v2):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    client, app, live = v2
    engine = app.state.application.sessions.kw["bind"].sync_engine
    marker = "提交失败验证-" + uuid4().hex

    def fail_commit(connection):
        raise OperationalError("commit", {}, Exception("synthetic"))

    event.listen(engine, "commit", fail_commit)
    try:
        result = request(
            client, live["operator"], "POST", "/api/v2/projects", json={"name": marker}
        )
        assert result.status_code == 503 and result.json()["code"] != 0
        assert result.json()["meta"]["recovery"]["write_outcome"] == "unknown"
    finally:
        event.remove(engine, "commit", fail_commit)
    with sessions(live["settings"])() as db:
        assert db.scalar(select(Project.id).where(Project.name == marker)) is None


def test_explicit_v2_sdk_reuses_browser_identity(live):
    from app.modules.access.outputs import IdentityOutput
    from office_client.contracts import ContractClient

    result = ContractClient(live["employee"]).request("GET", "/api/v2/me", IdentityOutput)
    assert result.code == 0 and result.data.id == live["employee_id"]
