"""Web 边界回归；隔离身份与数据库，验证真实路由、模板和表单契约。"""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from app.application.commands import validate_command
from app.common.errors import DomainError
from app.entrypoints.web.responses import instance_cookie
from app.entrypoints.web.session import SessionStore
from app.infrastructure.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def web(tmp_path, monkeypatch, request):
    secret = tmp_path / "web-secret"
    secret.write_bytes(b"isolated-test-secret-value-32-bytes")
    secret.chmod(0o600)
    settings = Settings(
        allow_lan_http_demo=getattr(request, "param", False),
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
        web_session_secret_file=secret,
    )
    store = SessionStore(settings.web_session_secret, settings.web_session_ttl)
    monkeypatch.setattr("app.entrypoints.web.console.SessionStore", lambda *args: store)
    monkeypatch.setattr("app.main.async_sessions", lambda settings: SimpleNamespace())
    app = create_app(settings)
    application = app.state.application
    application.verifier.verify_async = AsyncMock(
        return_value=SimpleNamespace(subject="operator", claims={})
    )
    application.verifier.verify_id_async = AsyncMock(
        return_value=SimpleNamespace(subject="operator", claims={"auth_time": int(time.time())})
    )

    async def operation(principal, name, payload, trace):
        validate_command(name, payload)
        if name == "session.register":
            return {"id": "current-session", "version": 1}
        if name == "session.change":
            return {"cleanup": "done"}
        if name == "me":
            return {"name": "测试管理员", "permissions": ["access.read", "access.manage"]}
        if name == "access.identity.pending":
            return {
                "items": [
                    {
                        "id": "pending-1",
                        "display_name": "新同事",
                        "subject": "kc-subject-value",
                        "first_seen_at": datetime.fromisoformat("2026-10-18T09:00:00+00:00"),
                        "last_seen_at": datetime.fromisoformat("2026-10-18T10:00:00+00:00"),
                    }
                ]
            }
        if name == "access.identity.claim":
            return {"id": "claimed-new", "version": 1}
        if name == "access.list":
            items = {
                "employees": [{"id": "person", "name": "员工", "active": True, "version": 2}],
                "permission-sets": [{"id": "bundle", "name": "权限集", "version": 2}],
                "permissions": [{"code": "record.create", "description": "上报记录"}],
            }.get(payload["kind"], [])
            return {"items": items, "has_more": False, "next": None}
        if name == "access.create":
            return {"id": "new-created", "version": 1}
        if name == "access.detail":
            return {
                "id": payload["identifier"],
                "kind": payload["kind"],
                "name": {
                    "employees": "员工",
                    "roles": "查阅角色",
                    "permission-sets": "项目上报权限集",
                }[payload["kind"]],
                "version": 2,
                "active": True if payload["kind"] == "employees" else None,
                "links": [],
                "permissions": [],
                "used_by": {"items": [], "has_more": False, "next": None},
            }
        if name == "access.links.get":
            return {"id": payload["identifier"], "version": 2, "ids": ["record.create"]}
        if (
            name in {"access.links.replace", "access.employee.update"}
            and payload["body"]["version"] != 2
        ):
            raise DomainError(409, "version_conflict", "配置已更新，请刷新")
        return {}

    application.call = AsyncMock(side_effect=operation)
    cookie = store.create({"access_token": "isolated", "csrf": "form-csrf"})
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    client.cookies.set(instance_cookie("office_web_session", settings.public_url), cookie)
    yield SimpleNamespace(
        app=app, application=application, store=store, client=client, cookie=cookie
    )
    client.close()


def test_dashboard_permissions_have_chinese_names_and_descriptions(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {"name": "普通员工", "permissions": ["record.create"]}
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/dashboard")
    assert response.status_code == 200
    assert "上报项目数据" in response.text and "向项目上报候选数据" in response.text
    assert "<table" in response.text
    assert all(call.args[1] == "me" for call in web.application.call.call_args_list)


def test_dashboard_shows_my_roles_above_permissions(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {
                "name": "普通员工",
                "permissions": ["record.create"],
                "roles": [
                    {
                        "id": "role-1",
                        "name": "普通员工",
                        "builtin_key": "employee",
                        "permission_count": 4,
                    }
                ],
            }
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/dashboard")
    assert response.status_code == 200
    roles_html = response.text[
        response.text.index('id="my-roles"') : response.text.index('id="my-permissions"')
    ]
    # 与「我的权限」共用同一套表格组件，不能退化成项目符号列表
    assert "我的角色" in roles_html and 'class="data-table"' in roles_html
    assert "<table" in roles_html and "<ul" not in roles_html
    assert "4 项</td>" in roles_html and "内置" in roles_html
    # 无 access.read 时不给「查看配置」入口，避免点了跳到无权页面
    assert "查看配置" not in roles_html
    assert response.text.index('id="my-roles"') < response.text.index('id="my-permissions"')


def test_dashboard_role_table_marks_custom_role_and_admin_action(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {
                "name": "管理员",
                "permissions": ["access.read"],
                "roles": [
                    {"id": "role-2", "name": "财务复核", "builtin_key": None, "permission_count": 2}
                ],
            }
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/dashboard")
    roles_html = response.text[
        response.text.index('id="my-roles"') : response.text.index('id="my-permissions"')
    ]
    assert "自定义" in roles_html and "2 项</td>" in roles_html
    assert 'href="/console/access/roles/role-2"' in roles_html


def test_dashboard_explains_when_no_role_is_assigned(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {"name": "新同事", "permissions": [], "roles": []}
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/dashboard")
    assert response.status_code == 200
    assert "尚未关联角色" in response.text
    # 空状态与「我的权限」同款，不渲染空表格
    assert 'class="empty"' in response.text
    assert 'id="my-roles"' in response.text


@pytest.mark.parametrize(
    "kind,relation", [("roles", "permission-sets"), ("permission-sets", "permissions")]
)
def test_creation_redirects_to_next_configuration_step(web, kind, relation):
    response = web.client.post(f"/console/{kind}/new", data={"csrf": "form-csrf", "name": "新配置"})
    assert response.status_code == 303
    assert response.headers["location"] == f"/console/links/{kind}/new-created/{relation}"


def test_named_configuration_rejects_blank_and_trims_whitespace():
    from app.modules.access.schemas import Named
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Named(name="   ")
    assert Named(name="  财务查阅  ").name == "财务查阅"


def test_editor_displays_name_and_search_preserves_selection(web):
    page = web.client.get("/console/links/permission-sets/bundle/permissions?q=上报")
    assert page.status_code == 200
    assert "项目上报权限集" in page.text and "查看详情与配置效果" in page.text
    assert 'value="record.create"' in page.text and "上报项目数据" in page.text
    calls = [c for c in web.application.call.call_args_list if c.args[1] == "access.list"]
    assert calls[0].args[2]["q"] == "上报"


def test_empty_detail_and_search_query_are_visible(web):
    page = web.client.get("/console/access/roles/example")
    assert page.status_code == 200
    assert "尚未关联权限集" in page.text and "当前没有关联的人员" in page.text
    web.client.get("/console/roles?q=财务")
    listing = [c for c in web.application.call.call_args_list if c.args[1] == "access.list"][-1]
    assert listing.args[2]["q"] == "财务"


@pytest.mark.parametrize("authenticated", [True, False])
def test_home_redirects_through_console_entry(web, authenticated):
    if not authenticated:
        web.client.cookies.clear()
    response = web.client.get("/?next=https://untrusted.invalid")
    assert response.status_code == 303
    assert response.headers["location"] == "/console"
    entry = web.client.get(response.headers["location"])
    assert entry.status_code == 303
    if authenticated:
        assert entry.headers["location"] == "/console/dashboard"
    else:
        assert entry.headers["location"].startswith(
            "https://identity.invalid/protocol/openid-connect/auth?"
        )
    assert "/" not in web.app.openapi()["paths"]


def test_missing_web_session_redirects_to_login(web):
    web.client.cookies.clear()
    response = web.client.get("/console/dashboard")
    assert response.status_code == 303
    assert response.headers["location"] == "/console"


@pytest.mark.parametrize("status", [403, 409, 422, 503])
def test_domain_failure_is_html_and_keeps_status(web, status):
    web.application.call.side_effect = DomainError(status, "permission_denied", "操作被拒绝")
    response = web.client.get("/console/employees")
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert "操作被拒绝" in response.text
    assert response.headers["x-trace-id"] in response.text


def test_expired_token_drops_session_without_redirect_loop(web):
    web.application.verifier.verify_async.side_effect = DomainError(
        401, "invalid_token", "凭证过期"
    )
    response = web.client.get("/console/dashboard")
    assert response.status_code == 303
    assert response.headers["location"] == "/console"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert web.client.get("/console").headers["location"].startswith("https://identity.invalid/")


def test_logout_requires_post_and_valid_csrf(web):
    assert web.client.get("/auth/logout").status_code == 405
    for data in ({}, {"csrf": "wrong"}):
        response = web.client.post("/auth/logout", data=data)
        assert response.status_code == 403
        assert "操作未成功" in response.text
        assert web.store.load(web.cookie)["access_token"] == "isolated"
    response = web.client.post("/auth/logout", data={"csrf": "form-csrf"})
    assert response.status_code == 303
    assert "对应身份登录均已结束" in web.client.get(response.headers["location"]).text
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert web.application.call.call_args_list[-1].args[1:3] == (
        "session.change",
        {
            "identifier": "current-session",
            "version": 1,
            "action": "disable",
            "reason": "用户主动退出控制台",
        },
    )
    assert web.client.get("/console/dashboard").status_code == 303


def test_form_error_has_html_and_never_calls_business_without_csrf(web):
    response = web.client.post("/console/roles/new", data={"name": "角色"})
    assert response.status_code == 403
    assert "操作未成功" in response.text
    web.application.call.assert_not_awaited()


def test_links_submit_preserves_page_version(web):
    response = web.client.post(
        "/console/links/employees/person/roles",
        data={"csrf": "form-csrf", "version": "1", "id_role": "role"},
    )
    assert response.status_code == 409
    assert "操作未成功" in response.text
    assert 'href="/console/links/employees/person/roles"' in response.text
    assert "不要重复提交旧表单" in response.text


def test_employee_submit_preserves_page_version(web):
    response = web.client.post(
        "/console/employees/person/update",
        data={"csrf": "form-csrf", "version": "1", "name": "员工"},
    )
    assert response.status_code == 409


def test_permission_editor_uses_codes_and_permission_payload(web):
    page = web.client.get("/console/links/permission-sets/bundle/permissions")
    assert page.status_code == 200
    assert 'value="record.create"' in page.text
    assert "上报记录" in page.text
    assert 'name="version" value="2"' in page.text
    response = web.client.post(
        "/console/links/permission-sets/bundle/permissions",
        data={"csrf": "form-csrf", "version": "2", "id_record.create": "record.create"},
    )
    assert response.status_code == 303
    assert web.application.call.call_args.args[2]["body"] == {
        "version": 2,
        "permissions": ["record.create"],
    }


@pytest.mark.parametrize("version", ["", "0", "-1", "1.2", "true", "1" * 40])
def test_bad_form_version_is_rejected_without_business_write(web, version):
    response = web.client.post(
        "/console/links/employees/person/roles",
        data={"csrf": "form-csrf", "version": version},
    )
    assert response.status_code == 422
    assert "操作未成功" in response.text
    assert all(c.args[1] != "access.links.replace" for c in web.application.call.call_args_list)


def test_auth_config_is_registered_once(web):
    from app.entrypoints.http.public import public_router
    from app.entrypoints.web.console import web_router

    # FastAPI可延迟组合子路由；从实际所属路由与公开HTTP行为验证，不依赖内部展开方式。
    assert (
        sum(
            getattr(route, "path", "") == "/auth/config"
            for route in public_router(web.application).routes
        )
        == 1
    )
    response = web.client.get("/auth/config")
    assert response.status_code == 200 and response.json()["issuer"] == "https://identity.invalid"
    assert "/auth/config" in web.app.openapi()["paths"]
    assert all(
        getattr(route, "path", "") != "/auth/config" for route in web_router(web.application).routes
    )


def test_read_only_account_has_no_write_controls(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {"name": "只读审查员", "permissions": ["access.read"]}
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/employees")
    assert response.status_code == 200
    assert "/console/employees/new" not in response.text
    assert "/console/employees/person/update" not in response.text
    response = web.client.get("/console/links/permission-sets/bundle/permissions")
    assert response.status_code == 200
    assert "保存授权" not in response.text


def test_paginated_editor_preserves_selected_items_outside_current_page(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "access.links.get":
            return {"id": "person", "version": 2, "ids": ["outside"]}
        if name == "access.list" and payload["kind"] == "roles":
            assert payload.get("after") == "cursor"
            return {
                "items": [{"id": "visible", "name": "当前页角色"}],
                "has_more": True,
                "next": "next-cursor",
            }
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/links/employees/person/roles?after=cursor")
    assert response.status_code == 200
    assert 'name="id_outside" value="outside"' in response.text
    assert 'form="grant-form"' in response.text and "checked" in response.text
    assert "筛选外已选择" in response.text
    assert "after=next-cursor" in response.text


def test_csrf_error_has_registered_business_code(web):
    response = web.client.post("/console/roles/new", data={"csrf": "错误"})
    assert response.status_code == 403
    assert "403003" in response.text


def test_unexpected_web_failure_is_sanitized_html(web):
    web.application.call.side_effect = RuntimeError("private-database-detail")
    response = web.client.get("/console/dashboard")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "private-database-detail" not in response.text
    assert response.headers["x-trace-id"] in response.text


@pytest.fixture
def identity_exchange(monkeypatch):
    """只替换远端令牌交换，浏览器Cookie与回调路由保持真实。"""
    payload = {"token_type": "Bearer", "access_token": "isolated"}
    response = httpx.Response(
        200, json=payload, request=httpx.Request("POST", "https://identity.invalid")
    )
    original = httpx.AsyncClient.post

    async def post(client, url, **kwargs):
        if str(url).startswith("https://identity.invalid"):
            return response
        return await original(client, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)


def test_callback_state_is_bound_to_browser_and_single_use(web, identity_exchange):
    web.client.cookies.clear()
    start = web.client.get("/console")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = "/auth/callback?code=isolated&state=" + state
    other = TestClient(web.app, base_url="https://testserver", follow_redirects=False)
    try:
        assert other.get(callback).status_code == 400
        assert web.client.get(callback).status_code == 303
        assert web.client.get(callback).status_code == 400
    finally:
        other.close()


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_parallel_login_tabs_do_not_overwrite_each_other(web, identity_exchange, order):
    web.client.cookies.clear()
    states = []
    for _ in range(2):
        start = web.client.get("/console")
        states.append(parse_qs(urlparse(start.headers["location"]).query)["state"][0])
    for index in order:
        callback = "/auth/callback?code=isolated&state=" + states[index]
        assert web.client.get(callback).status_code == 303
        assert states[index] not in web.store.pending


def test_pending_login_cookies_are_bounded_and_expired_ones_are_cleaned(web):
    from app.entrypoints.web.console import (
        LOGIN_COOKIE_LIMIT,
        login_cookie_name,
    )

    web.client.cookies.clear()
    states = []
    for _ in range(LOGIN_COOKIE_LIMIT):
        start = web.client.get("/console")
        assert start.status_code == 303
        states.append(parse_qs(urlparse(start.headers["location"]).query)["state"][0])
    assert web.client.get("/console").status_code == 503
    assert len(web.store.pending) == LOGIN_COOKIE_LIMIT
    # 到期后再打开入口，只清理本浏览器的无效Cookie，保留其他仍有效事务。
    value, _ = web.store.pending[states[0]]
    web.store.pending[states[0]] = value, 0
    assert web.client.get("/console").status_code == 303
    assert (
        login_cookie_name(states[0], web.application.settings.public_url) not in web.client.cookies
    )
    assert (
        sum(
            name.startswith(
                instance_cookie("office_web_login", web.application.settings.public_url) + "_"
            )
            for name in web.client.cookies
        )
        == LOGIN_COOKIE_LIMIT
    )


def test_swapped_login_cookie_is_rejected_without_consuming_state(web, identity_exchange):
    from app.entrypoints.web.console import login_cookie_name

    web.client.cookies.clear()
    starts = [web.client.get("/console"), web.client.get("/console")]
    states = [parse_qs(urlparse(r.headers["location"]).query)["state"][0] for r in starts]
    target = login_cookie_name(states[0], web.application.settings.public_url)
    original = web.client.cookies.get(target)
    other = web.client.cookies.get(
        login_cookie_name(states[1], web.application.settings.public_url)
    )
    domain = next(cookie.domain for cookie in web.client.cookies.jar if cookie.name == target)
    web.client.cookies.set(target, other, domain=domain, path="/")
    callback = "/auth/callback?code=isolated&state=" + states[0]
    assert web.client.get(callback).status_code == 400
    assert states[0] in web.store.pending
    web.client.cookies.set(target, original, domain=domain, path="/")
    assert web.client.get(callback).status_code == 303


def test_callback_wait_does_not_block_other_requests(web, monkeypatch):
    def sync_forbidden(*args, **kwargs):
        raise AssertionError("异步路由不得调用同步 HTTP")

    monkeypatch.setattr(httpx, "post", sync_forbidden)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        original = httpx.AsyncClient.post

        async def post(client, url, **kwargs):
            if str(url).startswith("https://identity.invalid"):
                entered.set()
                await release.wait()
                return httpx.Response(
                    200,
                    json={"token_type": "Bearer", "access_token": "isolated"},
                    request=httpx.Request("POST", url),
                )
            return await original(client, url, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=web.app), base_url="https://testserver"
        ) as client:
            start = await client.get("/console")
            state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
            task = asyncio.create_task(
                client.get("/auth/callback", params={"code": "isolated", "state": state})
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                assert (await client.get("/health/live")).status_code == 200
            finally:
                release.set()
                response = await task
            assert response.status_code == 303

    asyncio.run(run())


def test_pending_card_renders_real_datetime_and_escaped_name(web):
    response = web.client.get("/console/employees")
    assert response.status_code == 200
    assert "待认领登录" in response.text and "新同事" in response.text
    assert "2026-10-18 17:00" in response.text
    assert 'action="/console/employees/claim"' in response.text


def test_claim_requires_csrf_and_returns_to_employee_list(web):
    form = {"pending_id": "pending-1", "name": "新同事"}
    response = web.client.post("/console/employees/claim", data=form)
    assert response.status_code == 403
    assert all(
        call.args[1] != "access.identity.claim" for call in web.application.call.call_args_list
    )
    response = web.client.post("/console/employees/claim", data={**form, "csrf": "form-csrf"})
    assert response.status_code == 303
    assert response.headers["location"] == "/console/employees"


def test_audit_renders_readable_data_and_beijing_time(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "access.list" and payload["kind"] == "audit":
            return {
                "items": [
                    {
                        "actor_id": "actor",
                        "actor_name": "测试管理员",
                        "action": "access.changed",
                        "action_name": "授权配置变更",
                        "resource_id": "role",
                        "resource_name": "角色：财务",
                        "detail": {},
                        "detail_summary": "修改",
                        "trace_id": "trace",
                        "created_at": datetime.fromisoformat("2026-01-01T18:00:00+00:00"),
                    }
                ],
                "has_more": False,
                "next": None,
            }
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/audit?q=授权配置变更")
    assert response.status_code == 200
    assert "测试管理员" in response.text and "角色：财务" in response.text
    assert "2026-01-02 02:00" in response.text
    assert "失败或被拒绝" in response.text


def test_console_has_single_shared_workspace_and_native_fallback(web):
    response = web.client.get("/console/employees")
    assert response.status_code == 200
    assert response.text.count('<dialog id="workspace"') == 1
    assert response.text.count('<dialog id="action-confirm"') == 1
    assert 'href="/console/links/employees/person/roles"' in response.text
    assert 'data-confirm-message="确认停用' in response.text
    assert "prefers-reduced-motion" in response.text


def test_role_creation_keeps_native_form_and_has_modal_trigger(web):
    response = web.client.get("/console/roles")
    assert response.status_code == 200
    assert 'data-open-form="role-create"' in response.text
    assert 'method="post" action="/console/roles/new"' in response.text
    assert 'name="csrf" value="form-csrf"' in response.text
    assert 'name="name"' in response.text


def test_employee_rename_keeps_state_and_version_contract(web):
    response = web.client.get("/console/employees")
    assert response.status_code == 200
    assert "修改姓名" in response.text
    assert 'name="active" value="on"' in response.text
    response = web.client.post(
        "/console/employees/person/update",
        data={"csrf": "form-csrf", "version": "2", "name": "新姓名", "active": "on"},
    )
    assert response.status_code == 303
    call = next(
        call
        for call in web.application.call.call_args_list
        if call.args[1] == "access.employee.update"
    )
    assert call.args[2]["body"] == {"version": 2, "name": "新姓名", "active": True}


@pytest.mark.parametrize("assign_default", [True, False])
def test_employee_binding_forwards_explicit_default_choice(web, assign_default):
    form = {"csrf": "form-csrf", "name": "开通测试", "subject": "real-subject"}
    if assign_default:
        form["assign_default_role"] = "on"
    response = web.client.post("/console/employees/new", data=form)
    assert response.status_code == 303
    assert response.headers["location"] == "/console/employees"
    creation = next(
        call for call in web.application.call.call_args_list if call.args[1] == "access.create"
    )
    assert creation.args[2]["body"]["assign_default_role"] is assign_default


def test_revoked_session_does_not_reenter_old_sso(web):
    from app.modules.access.session_errors import SessionEnded

    web.application.call.side_effect = SessionEnded()
    response = web.client.get("/console/dashboard")
    assert response.status_code == 401
    assert "登录授权已结束" in response.text
    assert "location" not in response.headers
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_self_revocation_shows_result_without_using_revoked_identity(web):
    async def operation(principal, name, payload, trace):
        assert name == "session.change"
        return {"current": True, "cleanup": "failed"}

    web.application.call.side_effect = operation
    response = web.client.post(
        "/console/client-authorizations/self/change",
        data={"csrf": "form-csrf", "version": "1", "action": "revoke", "reason": "本人结束"},
    )
    assert response.status_code == 200 and "登录授权已结束" in response.text
    assert "清理尚未完成" in response.text
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert web.application.call.await_count == 1


def test_logout_cleanup_failure_still_clears_local_session(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "session.change":
            return {"cleanup": "failed"}
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.post("/auth/logout", data={"csrf": "form-csrf"})
    assert response.status_code == 303
    assert "身份登录清理暂未完成" in web.client.get(response.headers["location"]).text
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert web.client.get("/console/dashboard").status_code == 303


def test_normal_login_requests_and_validates_authentication_freshness(web, identity_exchange):
    web.client.cookies.clear()
    before = set(web.store.sessions)
    start = web.client.get("/console")
    parameters = parse_qs(urlparse(start.headers["location"]).query)
    state = parameters["state"][0]
    assert parameters["max_age"] == ["36000"]
    assert parameters["nonce"] == [web.store.pending[state][0]["nonce"]]
    web.application.verifier.verify_id_async.return_value = SimpleNamespace(
        subject="operator", claims={"auth_time": int(time.time()) - 36010}
    )
    response = web.client.get("/auth/callback?code=isolated&state=" + state)
    assert response.status_code == 502
    assert set(web.store.sessions) == before


def test_normal_login_rejects_different_id_token_subject(web, identity_exchange):
    web.client.cookies.clear()
    start = web.client.get("/console")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    web.application.verifier.verify_id_async.return_value = SimpleNamespace(
        subject="other", claims={"auth_time": int(time.time())}
    )
    assert web.client.get("/auth/callback?code=isolated&state=" + state).status_code == 502


@pytest.mark.parametrize("web", [True], indirect=True)
def test_http_acceptance_notice_preserves_transport_boundary(web):
    response = web.client.get("/console/dashboard")
    assert response.status_code == 200
    assert "HTTP 验收模式" in response.text
    assert "传输未加密，请勿用于正式业务" in response.text
    assert "开发演示" not in response.text


@pytest.mark.parametrize(
    "path,title",
    [("employees", "暂无人员"), ("roles", "暂无角色"), ("permission-sets", "暂无权限集")],
)
def test_list_empty_state_distinguishes_filters(web, path, title):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "access.list":
            return {"items": [], "next": None, "has_more": False}
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    empty = web.client.get(f"/console/{path}")
    assert empty.status_code == 200
    assert title in empty.text
    filtered = web.client.get(f"/console/{path}?q=不存在&limit=20")
    assert filtered.status_code == 200
    assert "没有匹配结果" in filtered.text
    assert "清除筛选" in filtered.text
    assert f"/console/{path}?limit=20" in filtered.text
