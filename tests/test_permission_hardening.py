"""细粒度权限与在线身份校验的负向测试；不使用真实凭证。"""

import asyncio

import httpx
import pytest
import test_web_console
from app.common.errors import DomainError
from app.infrastructure.config import Settings, TokenIntrospection
from app.modules.access.authentication import Principal, TokenVerifier
from app.modules.access.session_errors import SessionEnded

console_fixture = test_web_console.web


@pytest.fixture
def web(console_fixture):
    return console_fixture


def test_console_security_headers_and_audit_navigation(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {"name": "人员查看者", "permissions": ["access.read"]}
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/dashboard")
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert 'href="/console/employees"' in response.text
    assert 'href="/console/audit"' not in response.text


def test_name_maintainer_does_not_see_security_controls(web):
    original = web.application.call.side_effect

    async def operation(principal, name, payload, trace):
        if name == "me":
            return {"name": "人员维护", "permissions": ["access.read", "employee.update"]}
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    page = web.client.get("/console/employees")
    assert "修改姓名" in page.text
    assert "保存姓名" in page.text
    assert "绑定并开通" not in page.text
    assert 'data-confirm-message="确认停用' not in page.text
    assert "授权角色</a>" not in page.text


@pytest.mark.parametrize(
    "payload,status,expected",
    [
        ({"active": True}, 200, None),
        ({"active": False}, 200, "invalid_token"),
        ({"active": "true"}, 200, "identity_unavailable"),
        ({}, 200, "identity_unavailable"),
        ({"active": True}, 503, "identity_unavailable"),
        ({"active": True}, 401, "identity_unavailable"),
    ],
)
def test_online_identity_is_fail_closed(tmp_path, payload, status, expected):
    secret = tmp_path / "secret"
    secret.write_text("isolated-test-secret")
    secret.chmod(0o600)
    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
        token_introspection=TokenIntrospection(client_id="checker", client_secret_file=secret),
    )
    verifier = TokenVerifier(settings)
    verifier.verify = lambda token: Principal(settings.issuer, "employee", {})

    async def exercise():
        await verifier.http.aclose()
        calls = []

        def respond(request):
            calls.append(request)
            assert request.url.path.endswith("/token/introspect")
            assert b"client_id=checker" in request.content
            assert b"client_secret=" in request.content
            return httpx.Response(status, json=payload)

        verifier.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            if expected:
                with pytest.raises(DomainError) as error:
                    await verifier.verify_async("isolated-token")
                assert error.value.code == expected
                if expected == "invalid_token":
                    assert isinstance(error.value, SessionEnded)
                assert "isolated" not in error.value.message
            else:
                assert (await verifier.verify_async("isolated-token")).subject == "employee"
                await verifier.verify_async("isolated-token")
                assert len(calls) == 2  # 不缓存在线active结果，避免引入额外撤销窗口。
        finally:
            await verifier.aclose()

    asyncio.run(exercise())


def test_online_check_capacity_cancellation_and_network_failure(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("isolated-test-secret")
    secret.chmod(0o600)
    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
        auth_workers=1,
        token_introspection=TokenIntrospection(client_id="checker", client_secret_file=secret),
    )
    verifier = TokenVerifier(settings)
    verifier.verify = lambda token: Principal(settings.issuer, "employee", {})

    async def exercise():
        await verifier.http.aclose()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed(request):
            entered.set()
            await release.wait()
            raise httpx.ConnectError("must-not-expose-provider-detail")

        verifier.http = httpx.AsyncClient(transport=httpx.MockTransport(delayed))
        first = asyncio.create_task(verifier.verify_async("isolated-token"))
        try:
            async with asyncio.timeout(2):
                await entered.wait()
            with pytest.raises(DomainError) as busy:
                await verifier.verify_async("another-token")
            assert busy.value.code == "authentication_busy"
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            with pytest.raises(DomainError) as failure:
                await verifier.verify_async("next-token")
            assert failure.value.code == "identity_unavailable"
            assert "provider-detail" not in failure.value.message
            assert not verifier._online_capacity.locked()
        finally:
            first.cancel()
            await verifier.aclose()

    asyncio.run(exercise())
