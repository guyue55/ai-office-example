import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from app.infrastructure.database import sessions
from app.infrastructure.models import Audit
from app.main import create_app
from fastapi.testclient import TestClient
from live_environment import SERVICE_URL
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from office_client.auth import ClientError
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError


def test_real_browser_identity_and_no_anonymous_access(live):
    assert live["operator"].me()["active"] is True
    assert live["employee"].me()["id"] == live["employee_id"]
    response = httpx.get(SERVICE_URL + "/api/v2/me")
    assert response.status_code == 401
    assert response.headers["x-trace-id"]
    assert (
        httpx.get(
            SERVICE_URL + "/api/v2/me", headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )


def test_dynamic_crud_own_scope_and_idempotency(live, project):
    user, operator = live["employee"], live["operator"]
    schema = user.schema(project["id"], 1)
    assert schema["fields"][0]["id"] == "amount"
    key = uuid4().hex
    payload = {
        "project_id": project["id"],
        "schema_revision": 1,
        "values": {"amount": "123.45"},
        "idempotency_key": key,
    }
    first, repeat = user.submit(**payload), user.submit(**payload)
    assert first["id"] == repeat["id"]
    assert first["owner_id"] == live["employee_id"]
    assert first["status"] == "submitted"
    with pytest.raises(ClientError, match="409003"):
        user.submit(**{**payload, "values": {"amount": "999.00"}})
    other = operator.submit(**{**payload, "idempotency_key": uuid4().hex})
    with pytest.raises(ClientError, match="404"):
        user.request("GET", "/api/v2/records/" + other["id"])
    assert all(
        r["owner_id"] == live["employee_id"]
        for r in user.records(project_id=project["id"])["items"]
    )
    updated = user.request(
        "PATCH",
        "/api/v2/records/" + first["id"],
        json={"version": 1, "values": {"amount": "124.00"}},
    )
    assert updated["version"] == 2
    with pytest.raises(ClientError, match="409001"):
        user.request(
            "PATCH",
            "/api/v2/records/" + first["id"],
            json={"version": 1, "values": {"amount": "125.00"}},
        )
    archived = user.request("DELETE", "/api/v2/records/" + first["id"], params={"version": 2})
    assert archived["archived"]
    with pytest.raises(ClientError, match="404"):
        user.request("GET", "/api/v2/records/" + first["id"])


def test_schema_revision_and_validation(live, project):
    user, operator = live["employee"], live["operator"]
    payload = {
        "project_id": project["id"],
        "schema_revision": 1,
        "values": {"amount": "1.00", "invented": 1},
        "idempotency_key": uuid4().hex,
    }
    with pytest.raises(ClientError, match="422005"):
        user.submit(**payload)
    old = user.submit(**{**payload, "values": {"amount": "1.00"}})
    fields = project["schema"]["fields"] + [
        {
            "id": "new_field",
            "label": "项目新增",
            "type": "integer",
            "required": False,
            "description": "不同项目不同字段",
        }
    ]
    published = operator.request(
        "POST",
        f"/api/v2/projects/{project['id']}/schema",
        json={"expected_revision": 1, "fields": fields},
    )
    assert published["revision"] == 2
    with pytest.raises(ClientError, match="409002"):
        user.submit(**{**payload, "idempotency_key": uuid4().hex, "values": {"amount": "2.00"}})
    fresh = user.submit(
        **{
            **payload,
            "idempotency_key": uuid4().hex,
            "schema_revision": 2,
            "values": {"amount": "2.00", "new_field": 7},
        }
    )
    assert fresh["values"]["new_field"] == 7
    assert user.request("GET", "/api/v2/records/" + old["id"])["schema_revision"] == 1
    assert len(user.schema(project["id"], 1)["fields"]) == 2


def test_revoke_same_token_second_instance_and_restore(live, project):
    operator, user = live["operator"], live["employee"]
    original_token = user.oauth.access_token()
    bundle_id = live["bundle"]["id"]
    path = f"/api/v2/access/permission-sets/{bundle_id}/permissions"
    grant = operator.request("GET", path)
    changed = operator.request(
        "PUT",
        path,
        json={
            "version": grant["version"],
            "permissions": [p for p in live["permissions"] if p != "record.create"],
        },
    )
    try:
        assert user.oauth.access_token() == original_token
        with pytest.raises(ClientError, match="403001"):
            user.submit(
                project_id=project["id"],
                schema_revision=1,
                values={"amount": "1.00"},
                idempotency_key=uuid4().hex,
            )
        with TestClient(create_app(live["settings"])) as second:
            response = second.post(
                "/api/v2/records",
                headers={"Authorization": "Bearer " + original_token},
                json={
                    "project_id": project["id"],
                    "schema_revision": 1,
                    "values": {"amount": "1.00"},
                    "idempotency_key": uuid4().hex,
                },
            )
            assert response.status_code == 403
    finally:
        operator.request(
            "PUT", path, json={"version": changed["version"], "permissions": live["permissions"]}
        )
    assert user.submit(
        project_id=project["id"],
        schema_revision=1,
        values={"amount": "1.00"},
        idempotency_key=uuid4().hex,
    )["id"]


def test_employee_disable_last_admin_and_audit_atomicity(live):
    operator, user = live["operator"], live["employee"]
    path = f"/api/v2/access/employees/{live['employee_id']}"
    # 直接读详情，不依赖首页列表：本机测试库员工会累积，首页不保证包含目标。
    person = operator.request("GET", path)
    disabled = operator.request(
        "PATCH", path, json={"name": person["name"], "active": False, "version": person["version"]}
    )
    try:
        with pytest.raises(ClientError, match="403002"):
            user.me()
        response = httpx.post(
            SERVICE_URL + "/mcp",
            headers={
                "Authorization": "Bearer " + user.oauth.access_token(),
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert response.status_code == 401
    finally:
        operator.request(
            "PATCH",
            path,
            json={"name": person["name"], "active": True, "version": disabled["version"]},
        )
    admin = operator.me()
    before = admin["access_revision"]
    with pytest.raises(ClientError, match="409009"):
        operator.request(
            "PATCH",
            "/api/v2/access/employees/" + admin["id"],
            json={"name": admin["name"], "active": False, "version": admin["version"]},
        )
    assert operator.me()["access_revision"] == before
    with sessions(live["settings"])() as session:
        audits = session.scalars(select(Audit).where(Audit.action == "access.changed")).all()
        assert audits and max(a.detail.get("revision", 0) for a in audits) == before


def test_runtime_database_privilege_boundary(live):
    for sql in [
        "CREATE TABLE must_not_exist (id int)",
        "UPDATE audit_event SET action='tampered'",
        "DELETE FROM audit_event",
        "TRUNCATE audit_event",
        "UPDATE project_schema SET revision=999",
        "UPDATE permission SET description='tampered'",
        "SELECT * FROM alembic_version",
    ]:
        with sessions(live["settings"])() as session:
            with pytest.raises(ProgrammingError):
                session.execute(text(sql))
            session.rollback()
    with sessions(live["settings"])() as session:
        assert session.execute(text("SELECT current_user")).scalar() == "office_app"
        assert session.execute(text("SELECT version()")).scalar().startswith("PostgreSQL 18")


def test_real_mcp_same_identity_and_permissions(live, project):
    async def scenario():
        async with (
            httpx.AsyncClient(
                headers={"Authorization": "Bearer " + live["employee"].oauth.access_token()}
            ) as http,
            streamable_http_client(SERVICE_URL + "/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert "whoami" in [t.name for t in tools.tools]
            identity_tool = next(t for t in tools.tools if t.name == "whoami")
            assert "id" in identity_tool.model_dump(by_alias=True)["outputSchema"]["properties"]
            project_tool = next(t for t in tools.tools if t.name == "list_projects")
            input_schema = project_tool.model_dump(by_alias=True)["inputSchema"]
            assert input_schema["additionalProperties"] is False
            assert input_schema["properties"]["limit"]["maximum"] == 100
            invalid = await session.call_tool("list_projects", {"limit": "private-probe-value"})
            assert invalid.is_error and json.loads(invalid.content[0].text)["code"] == 422001
            assert "private-probe-value" not in invalid.content[0].text
            result = await session.call_tool("whoami", {})
            assert not result.is_error
            content = json.loads(result.content[0].text)
            assert content["id"] == live["employee_id"]
            assert content == await asyncio.to_thread(live["employee"].me)
            arguments = {
                "project_id": project["id"],
                "schema_revision": 1,
                "values": {"amount": "4.00"},
                "idempotency_key": uuid4().hex,
            }
            assert not (await session.call_tool("submit_record", arguments)).is_error
            operator = live["operator"]
            path = f"/api/v2/access/permission-sets/{live['bundle']['id']}/permissions"
            grant = await asyncio.to_thread(operator.request, "GET", path)
            changed = await asyncio.to_thread(
                operator.request,
                "PUT",
                path,
                json={
                    "version": grant["version"],
                    "permissions": [p for p in live["permissions"] if p != "record.create"],
                },
            )
            try:
                arguments["idempotency_key"] = uuid4().hex
                denied = await session.call_tool("submit_record", arguments)
                assert denied.is_error
                assert json.loads(denied.content[0].text)["code"] == 403001
            finally:
                await asyncio.to_thread(
                    operator.request,
                    "PUT",
                    path,
                    json={"version": changed["version"], "permissions": live["permissions"]},
                )

    asyncio.run(scenario())


def test_async_database_wait_does_not_block_other_requests(live, project):
    """真实行锁阻塞一个写入时，其他请求仍可完成，不能只验证 async 关键字。"""
    import time

    from app.infrastructure.database import async_sessions
    from app.infrastructure.models import Record

    user = live["employee"]
    record = user.submit(
        project_id=project["id"],
        schema_revision=1,
        values={"amount": "1.00"},
        idempotency_key=uuid4().hex,
    )
    token = user.oauth.access_token()

    async def scenario():
        factory = async_sessions(live["settings"])
        try:
            async with factory() as holder, httpx.AsyncClient(timeout=5) as http:
                await holder.execute(
                    select(Record).where(Record.id == record["id"]).with_for_update()
                )
                writing = asyncio.create_task(
                    http.patch(
                        SERVICE_URL + "/api/v2/records/" + record["id"],
                        headers={"Authorization": "Bearer " + token},
                        json={"version": 1, "values": {"amount": "2.00"}},
                    )
                )
                await asyncio.sleep(0.15)
                assert not writing.done()
                started = time.monotonic()
                try:
                    responses = await asyncio.wait_for(
                        asyncio.gather(
                            http.get(SERVICE_URL + "/health/live"),
                            http.get(
                                SERVICE_URL + "/api/v2/me",
                                headers={"Authorization": "Bearer " + token},
                            ),
                        ),
                        timeout=2,
                    )
                    assert all(r.status_code == 200 for r in responses)
                    assert time.monotonic() - started < 2
                    assert not writing.done()
                finally:
                    await holder.rollback()
                assert (await writing).status_code == 200
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())


def test_concurrent_same_key_has_one_record(live, project):
    token = live["employee"].oauth.access_token()
    payload = {
        "project_id": project["id"],
        "schema_revision": 1,
        "values": {"amount": "3.00"},
        "idempotency_key": uuid4().hex,
    }

    async def scenario():
        async with httpx.AsyncClient(
            timeout=10, headers={"Authorization": "Bearer " + token}
        ) as http:
            responses = await asyncio.gather(
                *[http.post(SERVICE_URL + "/api/v2/records", json=payload) for _ in range(8)]
            )
            assert all(r.status_code in (200, 201) for r in responses)
            assert len({r.json()["data"]["id"] for r in responses}) == 1

    asyncio.run(scenario())


def test_request_limits_and_safe_validation(live):
    response = httpx.post(SERVICE_URL + "/api/v2/projects", content=b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413
    response = httpx.post(
        SERVICE_URL + "/api/v2/projects",
        headers={"Authorization": "Bearer " + live["operator"].oauth.access_token()},
        json={"name": "sample", "unexpected": "sensitive-test-marker"},
    )
    assert response.status_code == 422
    assert "sensitive-test-marker" not in response.text


def test_client_cannot_escalate_access(live):
    with pytest.raises(ClientError, match="403001"):
        live["employee"].request("POST", "/api/v2/access/roles", json={"name": "不能自授管理员"})


def test_refresh_without_manual_key_exchange(live):
    user = live["employee"]
    tokens = dict(user.store.load())
    tokens["expires_at"] = 0
    user.store.save(tokens)
    assert user.me()["id"] == live["employee_id"]
    assert user.store.load()["expires_at"] > 0


def test_concurrent_updates_have_one_winner(live, project):
    user = live["employee"]
    record = user.submit(
        project_id=project["id"],
        schema_revision=1,
        values={"amount": "1.00"},
        idempotency_key=uuid4().hex,
    )
    token = user.oauth.access_token()

    async def scenario():
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + token}, timeout=10
        ) as http:
            responses = await asyncio.gather(
                *[
                    http.patch(
                        SERVICE_URL + "/api/v2/records/" + record["id"],
                        json={"version": 1, "values": {"amount": amount}},
                    )
                    for amount in ["2.00", "3.00"]
                ]
            )
            assert sorted(r.status_code for r in responses) == [200, 409]

    asyncio.run(scenario())


def test_other_owner_write_does_not_wait_for_record_lock(live, project):
    """本人权限的员工访问其他人的锁定记录，应直接404而非等待行锁。"""
    from app.infrastructure.database import async_sessions
    from app.infrastructure.models import Record

    record = live["operator"].submit(
        project_id=project["id"],
        schema_revision=1,
        values={"amount": "3.00"},
        idempotency_key=uuid4().hex,
    )
    token = live["employee"].oauth.access_token()

    async def run():
        factory = async_sessions(live["settings"])
        try:
            async with factory() as holder, httpx.AsyncClient() as http:
                await holder.execute(
                    select(Record).where(Record.id == record["id"]).with_for_update()
                )
                response = await asyncio.wait_for(
                    http.patch(
                        SERVICE_URL + "/api/v2/records/" + record["id"],
                        headers={"Authorization": "Bearer " + token},
                        json={"version": 1, "values": {"amount": "4.00"}},
                    ),
                    timeout=1,
                )
                assert response.status_code == 404
                await holder.rollback()
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(run())


def test_invalid_output_rolls_back_business_and_audit(live, monkeypatch):
    """错误返回结构必须在提交前拦截，HTTP/MCP均经过此边界。"""
    from app.common.errors import DomainError
    from app.infrastructure.models import Audit, Project
    from app.main import create_app
    from app.modules.data.service import Data

    original = Data.create_project
    created = []

    async def malformed(self, payload):
        result = await original(self, payload)
        created.append(result["id"])
        return {**result, "internal_secret": "must-not-return"}

    monkeypatch.setattr(Data, "create_project", malformed)
    app = create_app(live["settings"])
    application = app.state.application
    token = live["operator"].oauth.access_token()

    async def run():
        try:
            principal = await application.verifier.verify_async(token)
            with pytest.raises(DomainError) as caught:
                await application.call(
                    principal,
                    "project.create",
                    {"payload": {"name": "回滚验证-" + uuid4().hex}},
                    str(uuid4()),
                )
            assert caught.value.code == "invalid_response"
            async with application.sessions() as db:
                assert await db.get(Project, created[0]) is None
                assert await db.scalar(select(Audit).where(Audit.resource_id == created[0])) is None
        finally:
            await application.sessions.kw["bind"].dispose()

    asyncio.run(run())


def test_lost_submit_response_replays_original_key_once(live, project, monkeypatch):
    """模拟服务已提交但响应丢失，显式原键重试不得创建第二条。"""
    from app.infrastructure.models import Audit, Record
    from office_client import ServiceError

    user = live["employee"]
    payload = {
        "project_id": project["id"],
        "schema_revision": 1,
        "values": {"amount": "5.00"},
        "idempotency_key": uuid4().hex,
    }
    original = user.http.send
    saved = []

    def lost_response(*args, **kwargs):
        response = original(*args, **kwargs)
        assert response.status_code == 201
        try:
            response.read()
            saved.append(response.json()["data"]["id"])
        finally:
            response.close()
        raise httpx.ReadTimeout("模拟响应丢失", request=response.request)

    with monkeypatch.context() as patcher:
        patcher.setattr(user.http, "send", lost_response)
        with pytest.raises(ServiceError) as caught:
            user.submit(**payload)
        assert caught.value.outcome_unknown and caught.value.recovery == "replay_same_key"
        assert len(saved) == 1
    replay = user.submit(**payload)
    assert replay["id"] == saved[0]
    with sessions(live["settings"])() as db:
        rows = db.scalars(
            select(Record).where(
                Record.owner_id == live["employee_id"],
                Record.idempotency_key == payload["idempotency_key"],
            )
        ).all()
        audits = db.scalars(
            select(Audit).where(Audit.resource_id == saved[0], Audit.action == "record.created")
        ).all()
        assert len(rows) == 1 and len(audits) == 1


def test_project_patch_preserves_omitted_archive_state(live, project):
    operator = live["operator"]
    path = "/api/v2/projects/" + project["id"]
    current = None
    # 项目夹具创建后发布一次结构，返回的基础版本由项目列表定位。
    after = ""
    while current is None:
        page = operator.projects(after=after)
        current = next((p for p in page["items"] if p["id"] == project["id"]), None)
        if current is None:
            assert page["next"]
            after = page["next"]
    archived = operator.request(
        "PATCH", path, json={"version": current["version"], "archived": True}
    )
    renamed = operator.request(
        "PATCH", path, json={"version": archived["version"], "name": "已归档仅改名"}
    )
    assert renamed["archived"] is True and renamed["name"] == "已归档仅改名"
    with pytest.raises(ClientError, match="422"):
        operator.request("PATCH", path, json={"version": renamed["version"], "archived": None})
