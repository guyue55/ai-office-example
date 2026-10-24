"""真实数据库：预演回滚、摘要绑定、并发版本和记录冻结边界。"""

from uuid import uuid4

import pytest
from office_client import ServiceError


def test_project_and_schema_preview_does_not_write(live):
    admin, user = live["operator"], live["employee"]
    name = "对齐验证-" + uuid4().hex
    change = {"operation": "project.create", "payload": {"name": name}}
    with pytest.raises(ServiceError):
        user.preview_data_change(change)
    preview = admin.preview_data_change(change)
    assert "id" not in preview["after"]
    assert preview["before"] == {}
    with pytest.raises(ServiceError):
        admin.apply_data_change(
            {**change, "payload": {"name": name + "改变"}}, preview["confirmation_digest"]
        )
    created = admin.apply_data_change(preview["change"], preview["confirmation_digest"])["result"]
    assert created["name"] == name
    update = {
        "operation": "project.update",
        "payload": {
            "identifier": created["id"],
            "payload": {"version": created["version"], "name": name + "新版"},
        },
    }
    edit = admin.preview_data_change(update)
    assert edit["before"]["name"] == name
    assert edit["after"]["name"] == name + "新版"
    admin.apply_data_change(edit["change"], edit["confirmation_digest"])
    with pytest.raises(ServiceError):
        admin.apply_data_change(edit["change"], edit["confirmation_digest"])
    schema = {
        "operation": "schema.publish",
        "payload": {
            "identifier": created["id"],
            "payload": {
                "expected_revision": 0,
                "fields": [
                    {"id": "value", "label": "数值", "type": "decimal", "description": "测试数值"}
                ],
            },
        },
    }
    checked = admin.preview_data_change(schema)
    assert checked["after"]["revision"] == 1
    with pytest.raises(ServiceError):
        admin.schema(created["id"], 1)
    result = admin.apply_data_change(checked["change"], checked["confirmation_digest"])
    assert result["result"]["revision"] == 1


def test_record_preview_rollback_and_archive(live, project):
    user = live["employee"]
    row = user.submit(
        project_id=project["id"],
        schema_revision=1,
        values={"amount": "2.00"},
        idempotency_key=uuid4().hex,
    )
    change = {
        "operation": "record.update",
        "payload": {
            "identifier": row["id"],
            "payload": {"version": row["version"], "values": {"amount": "3.00"}},
        },
    }
    preview = user.preview_data_change(change)
    assert user.record(row["id"])["values"]["amount"] == "2.00"
    result = user.apply_data_change(preview["change"], preview["confirmation_digest"])["result"]
    assert result["values"]["amount"] == "3.00"
    archive = {
        "operation": "record.archive",
        "payload": {"identifier": row["id"], "version": result["version"]},
    }
    preview = user.preview_data_change(archive)
    assert user.record(row["id"])["archived"] is False
    user.apply_data_change(preview["change"], preview["confirmation_digest"])
    with pytest.raises(ServiceError):
        user.record(row["id"])


def test_mcp_data_tools_and_permissions(live):
    import asyncio
    import json

    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run():
        for actor in ("operator", "employee"):
            async with (
                httpx.AsyncClient(
                    headers={"Authorization": "Bearer " + live[actor].oauth.access_token()}
                ) as http,
                streamable_http_client(live["settings"].public_url + "/mcp", http_client=http) as (
                    read,
                    write,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool("pending_identities", {})
                assert result.is_error == (actor == "employee")
                change = {
                    "operation": "project.create",
                    "payload": {"name": "MCP对齐-" + uuid4().hex},
                }
                preview = await session.call_tool("preview_data_change", {"change": change})
                assert preview.is_error == (actor == "employee")
                if actor == "operator":
                    data = json.loads(preview.content[0].text)
                    result = await session.call_tool(
                        "apply_data_change",
                        {
                            "change": data["change"],
                            "confirmation_digest": data["confirmation_digest"],
                        },
                    )
                    assert not result.is_error, result.content
                    project_id = json.loads(result.content[0].text)["result"]["id"]
                    update = {
                        "operation": "project.update",
                        "payload": {
                            "identifier": project_id,
                            "payload": {"version": 1, "name": "MCP新版"},
                        },
                    }
                    updated = await session.call_tool("preview_data_change", {"change": update})
                    assert not updated.is_error, updated.content
                    normalized = json.loads(updated.content[0].text)
                    assert "archived" not in normalized["change"]["payload"]["payload"]
                    applied = await session.call_tool(
                        "apply_data_change",
                        {
                            "change": normalized["change"],
                            "confirmation_digest": normalized["confirmation_digest"],
                        },
                    )
                    assert not applied.is_error, applied.content
                    export = await session.call_tool(
                        "export_confirmed_records", {"project_id": project_id}
                    )
                    assert not export.is_error and json.loads(export.content[0].text)["items"] == []
                invalid = await session.call_tool(
                    "preview_data_change", {"change": {"operation": "sql.execute", "payload": {}}}
                )
                assert invalid.is_error
                invalid = await session.call_tool(
                    "export_confirmed_records", {"project_id": "unknown", "max_records": "100"}
                )
                assert invalid.is_error

    asyncio.run(run())
