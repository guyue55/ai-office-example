"""真实 MCP/HTTP/SDK 管理闭环；仅构造配置，不改动真实业务数据。"""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from live_environment import SERVICE_URL
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from office_client import ServiceError


def test_access_preview_digest_and_realtime_permissions(live):
    admin, user = live["operator"], live["employee"]
    change = {
        "operation": "access.create",
        "payload": {"kind": "roles", "body": {"name": "客户端闭环-" + uuid4().hex}},
    }
    preview = admin.preview_access_change(change)
    assert not admin.access_list("roles", q=change["payload"]["body"]["name"])["items"]
    with pytest.raises(ServiceError) as caught:
        admin.apply_access_change(change, "0" * 64)
    assert caught.value.status == 409
    tampered = {**change, "payload": {"kind": "roles", "body": {"name": "修改内容"}}}
    with pytest.raises(ServiceError) as caught:
        admin.apply_access_change(tampered, preview["confirmation_digest"])
    assert caught.value.status == 409
    with pytest.raises(ServiceError) as caught:
        user.preview_access_change(change)
    assert caught.value.status == 403
    role = admin.apply_access_change(change, preview["confirmation_digest"])
    assert admin.access_detail("roles", role["id"])["name"] == change["payload"]["body"]["name"]
    with pytest.raises(ServiceError) as caught:
        admin.apply_access_change(change, preview["confirmation_digest"])
    assert caught.value.status == 409


def test_mcp_management_chain_and_permission_revoke(live):
    admin, user = live["operator"], live["employee"]
    token = user.oauth.access_token()
    original = admin.access_links("employees", live["employee_id"], "roles")

    async def scenario():
        async with (
            httpx.AsyncClient(
                headers={"Authorization": "Bearer " + admin.oauth.access_token()}
            ) as http,
            streamable_http_client(SERVICE_URL + "/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            async def call(name, **args):
                result = await session.call_tool(name, args)
                assert not result.is_error, result.content
                return json.loads(result.content[0].text)

            async def apply(change):
                preview = await call("preview_access_change", change=change)
                return await call(
                    "apply_access_change",
                    change=change,
                    confirmation_digest=preview["confirmation_digest"],
                )

            tools = {t.name for t in (await session.list_tools()).tools}
            assert {"preview_access_change", "apply_access_change", "list_access"} <= tools
            listing = await call("list_access", kind="permissions", q="上报", limit=1)
            assert listing["items"][0]["code"] == "record.create"
            assert "has_more" in listing
            bundle = await apply(
                {
                    "operation": "access.create",
                    "payload": {
                        "kind": "permission-sets",
                        "body": {"name": "MCP权限-" + uuid4().hex},
                    },
                }
            )
            bundle = await apply(
                {
                    "operation": "access.links.replace",
                    "payload": {
                        "kind": "permission-sets",
                        "identifier": bundle["id"],
                        "relation": "permissions",
                        "body": {"version": bundle["version"], "permissions": ["record.read.all"]},
                    },
                }
            )
            role = await apply(
                {
                    "operation": "access.create",
                    "payload": {"kind": "roles", "body": {"name": "MCP角色-" + uuid4().hex}},
                }
            )
            await apply(
                {
                    "operation": "access.links.replace",
                    "payload": {
                        "kind": "roles",
                        "identifier": role["id"],
                        "relation": "permission-sets",
                        "body": {"version": role["version"], "ids": [bundle["id"]]},
                    },
                }
            )
            current = await call(
                "access_links", kind="employees", identifier=live["employee_id"], relation="roles"
            )
            try:
                await apply(
                    {
                        "operation": "access.links.replace",
                        "payload": {
                            "kind": "employees",
                            "identifier": live["employee_id"],
                            "relation": "roles",
                            "body": {
                                "version": current["version"],
                                "ids": current["ids"] + [role["id"]],
                            },
                        },
                    }
                )
                assert user.oauth.access_token() == token
                assert "record.read.all" in user.me()["permissions"]
                detail = await call("access_detail", kind="roles", identifier=role["id"])
                assert any(
                    p["code"] == "record.read.all" and p["name"] for p in detail["permissions"]
                )
                assert detail == admin.access_detail("roles", role["id"])
            finally:
                now = admin.access_links("employees", live["employee_id"], "roles")
                change = {
                    "operation": "access.links.replace",
                    "payload": {
                        "kind": "employees",
                        "identifier": live["employee_id"],
                        "relation": "roles",
                        "body": {"version": now["version"], "ids": original["ids"]},
                    },
                }
                preview = admin.preview_access_change(change)
                admin.apply_access_change(change, preview["confirmation_digest"])
            assert "record.read.all" not in user.me()["permissions"]
            assert (
                admin.access_links("employees", live["employee_id"], "roles")["ids"]
                == original["ids"]
            )

    asyncio.run(scenario())


def test_preview_is_bound_to_actor_and_global_authorization_revision(live):
    admin, user = live["operator"], live["employee"]
    bundle_id = live["bundle"]["id"]
    original = admin.access_links("permission-sets", bundle_id, "permissions")

    def grant(permissions):
        current = admin.access_links("permission-sets", bundle_id, "permissions")
        change = {
            "operation": "access.links.replace",
            "payload": {
                "kind": "permission-sets",
                "identifier": bundle_id,
                "relation": "permissions",
                "body": {"version": current["version"], "permissions": permissions},
            },
        }
        preview = admin.preview_access_change(change)
        admin.apply_access_change(change, preview["confirmation_digest"])

    try:
        grant(original["ids"] + ["access.read", "access.manage"])
        change = {
            "operation": "access.create",
            "payload": {"kind": "roles", "body": {"name": "摘要隔离-" + uuid4().hex}},
        }
        preview = admin.preview_access_change(change)
        with pytest.raises(ServiceError) as caught:
            user.apply_access_change(change, preview["confirmation_digest"])
        assert caught.value.status == 409
        own = user.preview_access_change(change)
        other = {
            "operation": "access.create",
            "payload": {"kind": "roles", "body": {"name": "修订变化-" + uuid4().hex}},
        }
        other_preview = admin.preview_access_change(other)
        admin.apply_access_change(other, other_preview["confirmation_digest"])
        with pytest.raises(ServiceError) as caught:
            user.apply_access_change(change, own["confirmation_digest"])
        assert caught.value.status == 409
    finally:
        grant(original["ids"])
    assert "access.manage" not in user.me()["permissions"]


def test_cli_management_uses_real_service_and_requires_confirmation(
    live, tmp_path, monkeypatch, capsys
):
    from contextlib import nullcontext

    from office_client import cli

    admin = live["operator"]
    monkeypatch.setattr(cli, "OfficeClient", lambda address: nullcontext(admin))

    def run(*arguments):
        monkeypatch.setattr("sys.argv", ["office", "--service", SERVICE_URL, *arguments])
        cli.main()
        return json.loads(capsys.readouterr().out)

    change = {
        "operation": "access.create",
        "payload": {"kind": "roles", "body": {"name": "CLI闭环-" + uuid4().hex}},
    }
    path = tmp_path / "change.json"
    path.write_text(json.dumps(change))
    preview = run("access-preview", "--file", str(path))
    assert not admin.access_list("roles", q=change["payload"]["body"]["name"])["items"]
    with pytest.raises(SystemExit) as caught:
        run("access-apply", "--file", str(path))
    assert caught.value.code == 2
    capsys.readouterr()
    created = run(
        "access-apply", "--file", str(path), "--confirm-digest", preview["confirmation_digest"]
    )
    detail = run("access-detail", "roles", created["id"])
    assert detail["name"] == change["payload"]["body"]["name"]
    links = run("access-links", "roles", created["id"], "permission-sets")
    assert links["ids"] == []
    listing = run("access-list", "roles", "--q", detail["name"], "--limit", "1")
    assert listing["items"][0]["id"] == created["id"]


def test_last_manager_must_keep_both_read_and_manage_permissions(live):
    """真实PG事务内移除读取权限再强制回滚；绝不持久改变现有授权。"""
    from app.common.errors import DomainError
    from app.infrastructure.models import SetPermission
    from app.main import create_app
    from app.modules.access.service import Access
    from sqlalchemy import delete

    application = create_app(live["settings"]).state.application
    token = live["operator"].oauth.access_token()

    async def scenario():
        principal = await application.verifier.verify_async(token)
        error = None
        async with application.sessions() as session:
            await session.begin()
            try:
                access = await Access.create(session, principal, "test-readable-manager", True)
                await session.execute(
                    delete(SetPermission).where(SetPermission.permission_code == "access.read")
                )
                try:
                    await access.changed(access.actor.id, {"reason": "仅事务内负向测试"})
                except DomainError as caught:
                    error = caught
            finally:
                await session.rollback()
        await application.sessions.kw["bind"].dispose()
        assert error is not None, "仅保留access.manage会让最后管理员失去自然语言和Web管理入口"
        assert error.status == 409 and error.code == "last_administrator"

    asyncio.run(scenario())
    assert "access.read" in live["operator"].me()["permissions"]


def test_business_employee_binding_and_disable_preview_chain(live):
    admin = live["operator"]
    issuer = admin.capabilities()["identity_issuer"]
    assert issuer == live["settings"].issuer
    # 验证业务绑定契约；此构造subject不声称在身份产品创建了可登录账号。
    change = {
        "operation": "access.create",
        "payload": {
            "kind": "employees",
            "body": {
                "name": "仅绑定测试-" + uuid4().hex[:8],
                "assign_default_role": False,
                "issuer": issuer,
                "subject": "unassigned-test-" + uuid4().hex,
            },
        },
    }
    wrong = {
        **change,
        "payload": {
            **change["payload"],
            "body": {**change["payload"]["body"], "issuer": "https://untrusted.invalid"},
        },
    }
    with pytest.raises(ServiceError) as caught:
        admin.preview_access_change(wrong)
    assert caught.value.status == 422
    preview = admin.preview_access_change(change)
    person = admin.apply_access_change(change, preview["confirmation_digest"])
    detail = admin.access_detail("employees", person["id"])
    assert detail["active"] and not detail["links"] and not detail["permissions"]
    change = {
        "operation": "access.employee.update",
        "payload": {
            "identifier": person["id"],
            "body": {"version": detail["version"], "name": detail["name"], "active": False},
        },
    }
    preview = admin.preview_access_change(change)
    assert preview["before"]["active"] and not preview["after"]["active"]
    admin.apply_access_change(change, preview["confirmation_digest"])
    assert not admin.access_detail("employees", person["id"])["active"]
