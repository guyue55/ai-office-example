"""账号控制台的认证用途、敏感响应、CSRF及公共契约负向验证。"""

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from app.application.commands import validate_command
from app.modules.access.account_schemas import AccountCreate, AccountPreviewInput
from pydantic import ValidationError

pytest_plugins = ("test_web_console",)  # 复用真实路由与会话夹具


@pytest.fixture
def accounts(web):
    principal = SimpleNamespace(subject="operator", claims={}, issuer="https://identity.invalid")
    web.application.verifier.verify_async = AsyncMock(return_value=principal)
    web.application.verifier.verify_id_async = AsyncMock(
        return_value=SimpleNamespace(subject="operator", claims={"auth_time": int(time.time())})
    )
    runner = SimpleNamespace(credential=AsyncMock(return_value="isolated-sensitive-password"))
    web.application.account_service = lambda: SimpleNamespace(runner=runner)

    async def call(principal, operation, payload, trace):
        validate_command(operation, payload)
        if operation == "me":
            return {
                "id": "operator",
                "name": "管理人员",
                "permissions": [
                    "access.read",
                    "access.manage",
                    "identity.read",
                    "identity.create",
                    "identity.credentials.manage",
                ],
            }
        if operation == "account.operation":
            return {
                "id": "operation",
                "version": 2,
                "kind": "create",
                "status": "awaiting_user",
                "step": "credential",
                "message": "等待准备",
                "employee_id": "person",
                "username": "new-user",
                "employee_name": "新同事",
                "next_url": "/console/accounts/operations/operation",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        return {}

    web.application.call = AsyncMock(side_effect=call)
    web.runner, web.principal = runner, principal
    return web


def test_credential_requires_csrf_and_operation_bound_recent_auth(accounts):
    path = "/console/accounts/operations/operation/credential"
    for form in ({"version": 2}, {"version": 2, "csrf": "form-csrf"}):
        response = accounts.client.post(path, data=form)
        assert response.status_code == 403
    session = accounts.store.load(accounts.cookie)
    for operation, age in (("another", 0), ("operation", 301)):
        session.update(credential_operation=operation, recent_auth=time.time() - age)
        assert (
            accounts.client.post(path, data={"version": 2, "csrf": "form-csrf"}).status_code == 403
        )
    accounts.runner.credential.assert_not_awaited()


def test_secret_is_once_only_no_store_and_never_public(accounts):
    session = accounts.store.load(accounts.cookie)
    session.update(credential_operation="operation", recent_auth=time.time())
    path = "/console/accounts/operations/operation/credential"
    response = accounts.client.post(path, data={"version": 2, "csrf": "form-csrf"})
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "isolated-sensitive-password" in response.text
    assert accounts.client.post(path, data={"version": 2, "csrf": "form-csrf"}).status_code == 403
    assert accounts.client.get("/api/v2/account-operations/operation/credential").status_code == 404
    assert (
        accounts.client.post("/api/v2/account-operations/operation/credential", json={}).status_code
        == 404
    )
    accounts.runner.credential.assert_awaited_once()
    assert all(
        call.args[1] != "account.credential" for call in accounts.application.call.call_args_list
    )


def test_reauthentication_binds_purpose_nonce_browser_and_original_subject(accounts, monkeypatch):
    async def exchange(self, *args, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://identity.invalid/token"),
            json={
                "token_type": "Bearer",
                "access_token": "isolated-access",
                "id_token": "isolated-id",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", exchange)
    path = "/console/accounts/operations/operation/verify"
    start = accounts.client.post(path, data={"csrf": "form-csrf"})
    params = parse_qs(urlsplit(start.headers["location"]).query)
    assert params["prompt"] == ["login"] and params["max_age"] == ["0"]
    state, nonce = params["state"][0], params["nonce"][0]
    pending = accounts.store.pending[state][0]
    assert pending["purpose"] == "credential" and pending["subject"] == "operator"
    callback = "/auth/callback?state=" + state + "&code=isolated-code"
    result = accounts.client.get(callback)
    assert result.status_code == 303
    assert result.headers["location"] == "/console/accounts/operations/operation"
    accounts.application.verifier.verify_id_async.assert_awaited_once_with("isolated-id", nonce)
    assert state not in accounts.store.pending
    assert accounts.client.get(callback).status_code == 400


@pytest.mark.parametrize("mismatch", ["subject", "auth_time"])
def test_reauthentication_rejects_identity_switch_or_stale_proof(accounts, monkeypatch, mismatch):
    async def exchange(self, *args, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://identity.invalid/token"),
            json={"token_type": "Bearer", "access_token": "isolated", "id_token": "isolated-id"},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", exchange)
    start = accounts.client.post(
        "/console/accounts/operations/operation/verify", data={"csrf": "form-csrf"}
    )
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    if mismatch == "subject":
        accounts.application.verifier.verify_async.return_value = SimpleNamespace(subject="another")
    else:
        accounts.application.verifier.verify_id_async.return_value = SimpleNamespace(
            subject="operator", claims={"auth_time": int(time.time()) - 600}
        )
    result = accounts.client.get("/auth/callback?state=" + state + "&code=isolated")
    assert result.status_code == 502  # 回调保持安全的统一登录失败页，不透露身份细节。
    assert "登录失败" in result.text
    assert "credential_operation" not in accounts.store.load(accounts.cookie)
    accounts.runner.credential.assert_not_awaited()


def test_account_commands_reject_arbitrary_upstream_and_secret_fields():
    for field in ("password", "realm", "return_url", "subject", "credentials", "enabled"):
        with pytest.raises(ValidationError):
            AccountPreviewInput.model_validate(
                {
                    "request": {
                        "kind": "create",
                        "name": "同事",
                        "username": "mz001",
                        field: "untrusted",
                    }
                }
            )
    normalized = AccountCreate(kind="create", name=" 同事 ", username=" MZ001 ")
    assert normalized.name == "同事" and normalized.username == "mz001"


@pytest.mark.parametrize("status", [409, 422])
def test_create_preview_error_restores_nonsecret_values(accounts, status):
    from app.common.errors import DomainError

    original = accounts.application.call.side_effect

    async def call(principal, operation, payload, trace):
        if operation == "account.capabilities":
            return {"enabled": True, "email_enabled": False}
        if operation == "account.preview":
            raise DomainError(status, "invalid_input", "请调整账号信息")
        return await original(principal, operation, payload, trace)

    accounts.application.call.side_effect = call
    response = accounts.client.post(
        "/console/accounts/new",
        data={
            "csrf": "form-csrf",
            "key": "same-request-key",
            "name": '<img src=x onerror="alert(1)">',
            "username": "person01",
            "email": "person@example.invalid",
            "mode": "temporary",
        },
    )
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "请调整账号信息" in response.text
    assert 'value="same-request-key"' in response.text
    assert 'value="person01"' in response.text
    assert 'value="person@example.invalid"' in response.text
    assert '<img src=x onerror="alert(1)">' not in response.text
    assert 'name="default_role" checked' not in response.text
    assert all(
        call.args[1] != "account.submit" for call in accounts.application.call.call_args_list
    )


@pytest.mark.parametrize("status", [401, 403, 503])
def test_create_preview_does_not_recover_security_or_service_failure(accounts, status):
    from app.common.errors import DomainError

    original = accounts.application.call.side_effect

    async def call(principal, operation, payload, trace):
        if operation == "account.preview":
            raise DomainError(status, "invalid_input", "操作暂不可用")
        return await original(principal, operation, payload, trace)

    accounts.application.call.side_effect = call
    response = accounts.client.post(
        "/console/accounts/new",
        data={"csrf": "form-csrf", "name": "同事", "username": "person01"},
    )
    assert response.status_code == (303 if status == 401 else status)
    assert 'data-server-form-error tabindex="-1"' not in response.text
    assert 'action="/console/accounts/new"' not in response.text
    assert all(
        call.args[1] != "account.submit" for call in accounts.application.call.call_args_list
    )
