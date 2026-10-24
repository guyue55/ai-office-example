"""评审问题的负向回归；仅隔离依赖，不放松生产认证或权限。"""

import ast
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.entrypoints.http.limits import BodyLimitMiddleware
from app.entrypoints.mcp.server import build_mcp
from app.infrastructure.config import Settings
from app.main import create_app
from app.modules.access.authentication import TokenVerifier
from app.modules.access.catalog import AUDIT_ACTIONS
from app.modules.access.service import Access
from app.modules.data.service import Data
from cryptography.hazmat.primitives.asymmetric import rsa
from office_client.product import PRODUCT
from sqlalchemy.exc import OperationalError


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
        operation_timeout=0.02,
    )


def test_removed_signing_key_is_not_indefinitely_cached(settings):
    verifier = TokenVerifier(settings)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="review", use="sig")
    verifier.jwks.fetch_data = lambda: {"keys": [jwk]}
    token = jwt.encode(
        {
            "iss": settings.issuer,
            "aud": settings.audience,
            "sub": "employee",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "scope": "office",
            "typ": "Bearer",
            "sid": "test-session",
            "azp": PRODUCT["client_id"],
        },
        key,
        algorithm="RS256",
        headers={"kid": "review"},
    )
    verifier.verify(token)
    # 隔离源直接返回新集合，模拟集合缓存刷新后的撤键状态。
    verifier.jwks.fetch_data = lambda: {"keys": []}
    verifier.jwks.jwk_set_cache.put(None)
    with pytest.raises(DomainError) as caught:
        verifier.verify(token)
    assert caught.value.status == 401


def test_identity_outage_is_retryable(settings):
    verifier = TokenVerifier(settings)

    def failed(token):
        raise jwt.PyJWKClientConnectionError("isolated")

    verifier.jwks.get_signing_key_from_jwt = failed
    with pytest.raises(DomainError) as caught:
        verifier.verify("synthetic")
    assert (caught.value.status, caught.value.code) == (503, "identity_unavailable")


def test_unauthorized_record_write_never_queries_or_locks():
    db = SimpleNamespace(scalar=AsyncMock())

    def denied(code):
        raise DomainError(403, "permission_denied", "拒绝")

    access = SimpleNamespace(db=db, allows=lambda code: False, need=denied)

    async def run():
        with pytest.raises(DomainError):
            await Data(access).record("other", mutate=True)
        db.scalar.assert_not_awaited()

    asyncio.run(run())


def test_body_timeout_and_compacted_frames():
    async def run():
        sent = []

        async def send(message):
            sent.append(message)

        async def slow():
            await asyncio.sleep(1)

        async def never(scope, receive, send):
            raise AssertionError("不应进入应用")

        await BodyLimitMiddleware(never, timeout=0.01)({"type": "http"}, slow, send)
        assert sent[0]["status"] == 408
        # 兜底路径必须与契约路径同格式：数字码失败体 + meta 追踪号，禁止旧字符串码回潮。
        body = json.loads(sent[1]["body"])
        assert body["code"] == 408001 and body["meta"]["trace_id"]
        frames = iter(
            [{"type": "http.request", "body": b"a", "more_body": True}] * 1000
            + [{"type": "http.request", "body": b"b", "more_body": False}]
        )

        async def receive():
            return next(frames)

        async def check(scope, receive, send):
            message = await receive()
            assert message["body"] == b"a" * 1000 + b"b" and not message["more_body"]

        sent.clear()
        await BodyLimitMiddleware(check)({"type": "http"}, receive, send)
        oversized = bytearray()
        for _ in range(1025):
            oversized.extend(b"x" * 1024)
        too_large = iter([{"type": "http.request", "body": bytes(oversized), "more_body": False}])

        async def big_receive():
            return next(too_large)

        await BodyLimitMiddleware(never)({"type": "http"}, big_receive, send)
        large_body = json.loads(sent[1]["body"])
        assert large_body["code"] == 413001 and large_body["meta"]["trace_id"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (None, 503, 503006),
        (OperationalError("isolated", {}, Exception("private-input")), 503, 503001),
        (RuntimeError("private-input"), 500, 500001),
    ],
)
def test_ready_requires_initialized_revision(settings, monkeypatch, failure, status, code):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def scalar(self, *args):
            if failure is not None:
                raise failure

    monkeypatch.setattr("app.main.async_sessions", lambda settings: lambda: Session())

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://testserver"
        ) as client:
            response = await client.get("/health/ready")
            assert response.status_code == status
            assert response.json()["code"] == code
            assert response.headers["X-Trace-ID"] == response.json()["meta"]["trace_id"]
            assert "private-input" not in response.text

    asyncio.run(run())


def test_mcp_dependency_failure_is_not_login_rejection(settings):
    async def failed(token):
        raise DomainError(503, "identity_unavailable", "暂不可用")

    application = SimpleNamespace(settings=settings, verifier=SimpleNamespace(verify_async=failed))
    server, app = build_mcp(application)

    async def run():
        async with (
            server.session_manager.run(),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://service.invalid"
            ) as client,
        ):
            response = await client.post(
                "/mcp", headers={"Authorization": "Bearer synthetic"}, json={}
            )
            assert response.status_code == 503
            assert response.json()["code"] == 503004

    asyncio.run(run())


def test_operation_deadline_rolls_back_transaction(settings, monkeypatch):
    exited = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, kind, *args):
            exited.append(kind)

    async def blocked(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr("app.application.service.Access.create", blocked)
    application = Application(settings, SimpleNamespace(begin=Transaction), None)

    async def run():
        with pytest.raises(DomainError) as caught:
            await application.call(None, "me", {}, "test")
        assert caught.value.code == "operation_timeout"
        assert exited and issubclass(exited[0], asyncio.CancelledError)

    asyncio.run(run())


def test_pending_trace_runs_only_after_rejected_operation_releases_session(settings, monkeypatch):
    """留痕必须晚于拒绝事务退出，连接池只有一条连接也不能发生嵌套借用。"""
    events = []

    class Transaction:
        async def __aenter__(self):
            assert events == ["released"]
            events.append("trace_started")
            return self

        async def __aexit__(self, kind, *_):
            assert kind is None
            events.append("trace_committed")

    application = Application(settings, SimpleNamespace(begin=Transaction), None)

    async def rejected(*args, **kwargs):
        events.append("released")
        raise DomainError(403, "inactive_employee", "员工未开通")

    application._execute = rejected
    trace = AsyncMock()
    monkeypatch.setattr(Access, "trace_pending_identity", trace)

    async def run():
        with pytest.raises(DomainError) as caught:
            await application.call(object(), "project.list", {}, "trace")
        assert caught.value.code == "inactive_employee"
        assert events == ["released", "trace_started", "trace_committed"]
        trace.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [TimeoutError(), OperationalError("stmt", None, Exception())])
def test_pending_trace_failure_preserves_rejection(settings, monkeypatch, failure):
    class Transaction:
        async def __aenter__(self):
            raise failure

        async def __aexit__(self, *args):
            pass

    application = Application(settings, SimpleNamespace(begin=Transaction), None)
    application._execute = AsyncMock(
        side_effect=DomainError(403, "inactive_employee", "员工未开通")
    )

    async def run():
        with pytest.raises(DomainError) as caught:
            await application.call(object(), "project.list", {}, "trace")
        assert caught.value.code == "inactive_employee"

    asyncio.run(run())


def test_pending_trace_skips_already_linked_or_inactive_identity():
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[object(), "link-id"]), execute=AsyncMock()
    )
    principal = SimpleNamespace(issuer="https://identity.invalid", subject="known")

    asyncio.run(Access.trace_pending_identity(session, principal))
    session.execute.assert_not_awaited()


def test_audit_action_catalog_covers_actual_written_actions():
    """扫描真实 audit 调用，不能仅验证人为复制的一组动作编码。"""
    root = Path(__file__).resolve().parents[1] / "office-data-service/app/modules"
    actions = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "audit"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                actions.add(node.args[0].value)
    assert actions
    assert actions <= AUDIT_ACTIONS.keys()
