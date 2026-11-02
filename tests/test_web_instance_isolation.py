"""跨端口共享浏览器 Cookie，验证实例隔离及登录恢复。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app.infrastructure.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_second_instance_preserves_first_login_transaction(tmp_path):
    secret = tmp_path / "session"
    secret.write_bytes(b"synthetic-session-key-at-least-32-bytes")
    secret.chmod(0o600)
    apps = []
    for port in (18001, 18002):
        settings = Settings(
            database_user="office_app",
            database_password_file=tmp_path / "unused",
            issuer="https://identity.invalid",
            audience=f"https://testserver:{port}/mcp",
            public_url=f"https://testserver:{port}",
            web_session_secret_file=secret,
        )
        with patch("app.main.async_sessions", lambda settings: SimpleNamespace()):
            apps.append(create_app(settings))
    first = TestClient(apps[0], base_url="https://testserver:18001", follow_redirects=False)
    second = TestClient(apps[1], base_url="https://testserver:18002", follow_redirects=False)
    try:
        start = first.get("/console")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        original = dict(first.cookies)
        second.cookies.update(first.cookies)
        second.get("/console")
        assert all(second.cookies.get(name) == value for name, value in original.items())
        first.cookies = second.cookies
        # 到达令牌交换并失败 502，证明浏览器绑定仍有效，而非错误地返回绑定失败 400。
        with patch("httpx.AsyncClient.post", side_effect=ValueError("synthetic")):
            response = first.get("/auth/callback", params={"state": state, "code": "synthetic"})
        assert response.status_code == 502
    finally:
        first.close()
        second.close()
        for app in apps:
            asyncio.run(app.state.application.verifier.aclose())


def test_expired_callback_has_safe_recovery_link(tmp_path):
    secret = tmp_path / "session"
    secret.write_bytes(b"synthetic-session-key-at-least-32-bytes")
    secret.chmod(0o600)
    settings = Settings(
        web_session_secret_file=secret,
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    with patch("app.main.async_sessions", lambda settings: SimpleNamespace()):
        app = create_app(settings)
    try:
        with_recovery = TestClient(app).get("/auth/callback", params={"state": "expired"})
        assert with_recovery.status_code == 400
        assert 'href="/console"' in with_recovery.text
        assert "重新登录" in with_recovery.text
    finally:
        asyncio.run(app.state.application.verifier.aclose())


def test_cookie_namespace_normalizes_default_port_and_isolates_instances():
    from app.entrypoints.web.responses import instance_cookie

    a = instance_cookie("session", "https://example.com")
    assert a == instance_cookie("session", "https://example.com:443/")
    assert a != instance_cookie("session", "https://example.com:8443")
    assert a != instance_cookie("session", "http://example.com:443")
