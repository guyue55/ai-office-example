"""本机真实确认／更正链路，测试权限仅临时授给专用本地账号。"""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from app.infrastructure.database import sessions
from app.infrastructure.models import RecordConfirmation
from live_environment import SERVICE_URL
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from office_client.auth import ClientError
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError


@pytest.fixture
def confirmer(live):
    admin = live["operator"]
    identifier = admin.me()["id"]
    path = f"/api/v2/access/employees/{identifier}/roles"
    before = admin.request("GET", path)
    bundle = admin.request(
        "POST", "/api/v2/access/permission-sets", json={"name": "确认测试-" + uuid4().hex}
    )
    admin.request(
        "PUT",
        f"/api/v2/access/permission-sets/{bundle['id']}/permissions",
        json={"version": bundle["version"], "permissions": ["record.confirm"]},
    )
    role = admin.request("POST", "/api/v2/access/roles", json={"name": "确认角色-" + uuid4().hex})
    admin.request(
        "PUT",
        f"/api/v2/access/roles/{role['id']}/permission-sets",
        json={"version": role["version"], "ids": [bundle["id"]]},
    )
    admin.request(
        "PUT", path, json={"version": before["version"], "ids": before["ids"] + [role["id"]]}
    )
    try:
        yield admin
    finally:
        current = admin.request("GET", path)
        admin.request("PUT", path, json={"version": current["version"], "ids": before["ids"]})


def test_confirmation_correction_preserves_published_value(live, project, confirmer):
    user = live["employee"]
    record = user.submit(
        project_id=project["id"],
        schema_revision=1,
        values={"amount": "2.00"},
        idempotency_key=uuid4().hex,
    )
    with pytest.raises(ClientError, match="403"):
        user.confirm(record["id"], 1)
    first = confirmer.confirm(record["id"], 1)
    assert first["revision"] == 1 and first["values"]["amount"] == "2.00"
    with pytest.raises(ClientError, match="409004"):
        user.request(
            "PATCH",
            "/api/v2/records/" + record["id"],
            json={"version": 2, "values": {"amount": "3.00"}},
        )
    corrected = user.correct(
        record["id"], version=2, values={"amount": "3.00"}, reason="纠正测试金额"
    )
    assert corrected["status"] == "submitted" and corrected["version"] == 3
    assert user.confirmed(record["id"])["values"]["amount"] == "2.00"
    assert len(user.history(record["id"])["items"]) == 1

    async def confirm_by_mcp():
        async with (
            httpx.AsyncClient(
                headers={"Authorization": "Bearer " + confirmer.oauth.access_token()}
            ) as http,
            streamable_http_client(SERVICE_URL + "/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "confirm_record", {"record_id": record["id"], "version": 3}
            )
            assert not result.is_error
            return json.loads(result.content[0].text)

    second = asyncio.run(confirm_by_mcp())
    assert second["revision"] == 2 and second["values"]["amount"] == "3.00"
    assert user.confirmed(record["id"])["revision"] == 2
    page = user.history(record["id"], limit=1)
    assert page["items"][0]["values"]["amount"] == "2.00" and page["next"] == "1"
    assert user.history(record["id"], after_revision=1)["items"][0]["revision"] == 2
    with pytest.raises(ClientError, match="409004"):
        user.request("DELETE", "/api/v2/records/" + record["id"], params={"version": 4})
    with sessions(live["settings"])() as db:
        rows = db.scalars(
            select(RecordConfirmation).where(RecordConfirmation.record_id == record["id"])
        ).all()
        assert len(rows) == 2
    for sql in [
        "UPDATE record_confirmation SET reason='tampered'",
        "DELETE FROM record_confirmation",
        "TRUNCATE record_confirmation",
    ]:
        with sessions(live["settings"])() as db, pytest.raises(ProgrammingError):
            db.execute(text(sql))


def test_concurrent_confirm_has_one_winner(live, project, confirmer):
    record = confirmer.submit(
        project_id=project["id"],
        schema_revision=1,
        values={"amount": "1.00"},
        idempotency_key=uuid4().hex,
    )

    async def run():
        async with httpx.AsyncClient() as http:
            return await asyncio.gather(
                *[
                    http.post(
                        f"{SERVICE_URL}/api/v2/records/{record['id']}/confirm",
                        headers={"Authorization": "Bearer " + confirmer.oauth.access_token()},
                        json={"version": 1},
                    )
                    for _ in range(2)
                ]
            )

    results = asyncio.run(run())
    assert sorted(r.status_code for r in results) == [200, 409]
    assert len(confirmer.history(record["id"])["items"]) == 1


def test_confirm_action_respects_read_scope_and_live_revocation(live, project):
    admin, user = live["operator"], live["employee"]
    path = f"/api/v2/access/permission-sets/{live['bundle']['id']}/permissions"
    before = admin.request("GET", path)
    admin.request(
        "PUT",
        path,
        json={
            "version": before["version"],
            "permissions": live["permissions"] + ["record.confirm"],
        },
    )
    try:
        other = admin.submit(
            project_id=project["id"],
            schema_revision=1,
            values={"amount": "7.00"},
            idempotency_key=uuid4().hex,
        )
        with pytest.raises(ClientError, match="404"):
            user.confirm(other["id"], 1)
        own = user.submit(
            project_id=project["id"],
            schema_revision=1,
            values={"amount": "8.00"},
            idempotency_key=uuid4().hex,
        )
        assert user.confirm(own["id"], 1)["confirmed_by"] == live["employee_id"]
        current = admin.request("GET", path)
        admin.request(
            "PUT", path, json={"version": current["version"], "permissions": live["permissions"]}
        )
        with pytest.raises(ClientError, match="403"):
            user.confirm(other["id"], 1)
    finally:
        current = admin.request("GET", path)
        admin.request(
            "PUT", path, json={"version": current["version"], "permissions": live["permissions"]}
        )
