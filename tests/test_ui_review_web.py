"""独立UI审阅整改：确认内容、权限组合、无脚本定位与分页。"""

import pytest
from app.entrypoints.web.navigation import page_location
from app.entrypoints.web.responses import TEMPLATES
from fastapi import Request
from test_web_console import web  # noqa: F401


@pytest.mark.parametrize(
    "path,section,title",
    [
        ("/console/access/employees/person", "employees", "授权详情"),
        ("/console/links/roles/role/permission-sets", "roles", "授权关联"),
        ("/console/accounts/employees/person", "employees", "账号详情"),
        ("/console/accounts/operations/op", "account-operations", "账号处理进度"),
        ("/console/security", "", "账号与安全"),
        ("/console/assistant/task", "assistant", "任务详情"),
    ],
)
def test_page_location(path, section, title):
    assert page_location(path)["section"] == section
    assert page_location(path)["title"] == title


@pytest.mark.parametrize("permissions", [[], ["agent.run"]])
def test_employee_dashboard_uses_real_capabilities(web, permissions):  # noqa: F811
    original = web.application.call.side_effect

    async def call(principal, operation, payload, trace):
        if operation == "me":
            return {"id": "person", "name": "普通同事", "permissions": permissions}
        return await original(principal, operation, payload, trace)

    web.application.call.side_effect = call
    response = web.client.get("/console/dashboard")
    assert response.status_code == 200
    assert "管理我的客户端授权" in response.text
    assert ("开始使用智能助手" in response.text) == ("agent.run" in permissions)
    assert "业务操作通过客户端技能使用" not in response.text
    assert "查看人员及角色" not in response.text


@pytest.mark.parametrize("pages", [1, 5])
def test_pagination_keeps_only_useful_controls(pages):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/console/roles",
            "query_string": b"q=abc",
            "headers": [],
        }
    )
    value = TEMPLATES.env.get_template("pagination.html").render(
        request=request,
        listing={
            "pagination": {
                "page": 1,
                "pages": pages,
                "total": pages * 10,
                "page_size": 10,
                "start": 1,
                "end": 10,
            }
        },
    )
    assert 'class="page-size"' in value
    assert ('class="page-links"' in value) == (pages > 1)
    assert ('class="page-jump"' in value) == (pages > 1)
    assert 'name="q" value="abc"' in value


@pytest.mark.parametrize("kind", ["create", "link", "update"])
def test_account_confirmation_shows_verified_target_and_values(web, kind):  # noqa: F811
    original = web.application.call.side_effect
    calls = []

    async def call(principal, operation, payload, trace):
        calls.append((operation, payload))
        if operation == "me":
            return {
                "name": "管理员",
                "permissions": [
                    "access.read",
                    "access.manage",
                    "identity.read",
                    "identity.create",
                    "identity.update",
                ],
            }
        if operation == "account.capabilities":
            return {"enabled": True, "email_enabled": True}
        if operation == "account.search":
            return {
                "items": [
                    {
                        "id": "remote-one",
                        "name": "服务端核实姓名",
                        "username": "verified01",
                        "email": "old@example.invalid",
                    }
                ]
            }
        if operation == "account.detail":
            return {
                "version": 2,
                "name": "原业务姓名",
                "account": {"username": "verified01", "email": "old@example.invalid"},
                "available": True,
            }
        if operation == "account.preview":
            return {
                "request": payload["request"],
                "effects": ["本次角色：普通员工"],
                "confirmation_digest": "f" * 64,
            }
        return await original(principal, operation, payload, trace)

    web.application.call.side_effect = call
    data = {
        "csrf": "form-csrf",
        "name": "新业务姓名",
        "email": "new@example.invalid",
        "username": "person01",
        "mode": "email",
        "default_role": "on",
        "key": "original-key",
    }
    if kind == "link":
        path = "/console/accounts/link"
        data.update(remote_id="remote-one", lookup="verified")
    elif kind == "update":
        path = "/console/accounts/employees/person/preview"
        data.update(kind="update", version="2")
    else:
        path = "/console/accounts/new"
    response = web.client.post(path, data=data)
    assert response.status_code == 200
    assert "新业务姓名" in response.text
    if kind == "link":
        assert "服务端核实姓名" in response.text and "verified01" in response.text
        assert "remote-one" in response.text
    elif kind == "update":
        assert all(
            value in response.text
            for value in (
                "原业务姓名",
                "old@example.invalid",
                "new@example.invalid",
                "姓名变更后",
                "邮箱变更前",
            )
        )
    else:
        assert "new@example.invalid" in response.text and "邮件设置密码" in response.text
        assert 'value="original-key"' in response.text
    assert 'name="digest" value="' + "f" * 64 + '"' in response.text
    assert not any(operation == "account.submit" for operation, _ in calls)


def test_link_mismatched_id_is_rejected_before_preview(web):  # noqa: F811
    original = web.application.call.side_effect
    calls = []

    async def call(principal, operation, payload, trace):
        calls.append(operation)
        if operation == "account.search":
            return {"items": [{"id": "verified-other", "name": "同名人员"}]}
        return await original(principal, operation, payload, trace)

    web.application.call.side_effect = call
    response = web.client.post(
        "/console/accounts/link",
        data={"csrf": "form-csrf", "lookup": "person", "remote_id": "tampered", "name": "冒填姓名"},
    )
    assert response.status_code == 409
    assert "account.preview" not in calls and "account.submit" not in calls


@pytest.mark.parametrize(
    "route", ["/console/access/employees/person", "/console/links/employees/person/roles"]
)
def test_detail_location_is_rendered_before_javascript(web, route):  # noqa: F811
    response = web.client.get(route)
    assert response.status_code == 200
    assert '<a href="/console/employees" class="active" aria-current="page">' in response.text
    title = "授权详情" if "/access/" in route else "授权关联"
    assert f'<strong id="shell-current-page">{title}</strong>' in response.text


@pytest.mark.parametrize("version,account", [(3, {"email": "old@example.invalid"}), (2, None)])
def test_update_confirmation_does_not_mix_stale_or_unknown_source(web, version, account):  # noqa: F811
    original = web.application.call.side_effect
    calls = []

    async def call(principal, operation, payload, trace):
        calls.append(operation)
        if operation == "account.preview":
            return {"request": payload["request"], "effects": [], "confirmation_digest": "f" * 64}
        if operation == "account.detail":
            return {"version": version, "name": "原姓名", "account": account}
        return await original(principal, operation, payload, trace)

    web.application.call.side_effect = call
    response = web.client.post(
        "/console/accounts/employees/person/preview",
        data={
            "csrf": "form-csrf",
            "kind": "update",
            "version": "2",
            "name": "新姓名",
            "email": "new@example.invalid",
        },
    )
    assert response.status_code == 409
    assert "account.submit" not in calls
