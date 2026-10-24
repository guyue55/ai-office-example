"""认证引导只输出必要事实；浏览器授权仍使用同机PKCE及state校验。"""

import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from office_client import cli
from office_client.auth import ClientError, OAuth, trusted_url
from office_client.product import PRODUCT


class Store:
    def __init__(self, value=None):
        self.value = value

    def load(self):
        return self.value

    def save(self, value):
        self.value = value


@pytest.mark.parametrize(
    "value,state",
    [
        (None, "missing"),
        ({"access_token": "synthetic", "expires_at": 1}, "expired"),
        ({"access_token": "synthetic", "expires_at": time.time() + 10000}, "present"),
        ({"access_token": "synthetic", "expires_at": float("nan")}, "invalid"),
        ({"access_token": "synthetic", "expires_at": True}, "invalid"),
        (["synthetic"], "invalid"),
    ],
)
def test_status_never_contacts_service_or_prints_secret(value, state):
    oauth = OAuth("https://service.example", Store(value))
    oauth.http.close()
    oauth.http = Mock()
    status = oauth.status()
    assert status["credential_state"] == state and status["online_verified"] is False
    assert "synthetic" not in json.dumps(status)
    oauth.http.get.assert_not_called()
    oauth.http.post.assert_not_called()


def test_no_browser_login_still_verifies_callback_and_exchanges_in_memory():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    store, urls = Store(), Queue()
    browser = Mock()
    oauth = OAuth("https://service.example", store, browser_open=browser)
    oauth._config = {
        "issuer": "https://identity.example/realms/office",
        "client_id": PRODUCT["client_id"],
        "redirect_uri": f"http://127.0.0.1:{port}/callback",
        "scope": "openid office",
    }
    oauth.http.close()
    posts = []

    def handle(request):
        posts.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "expires_in": 300,
            },
        )

    oauth.http = httpx.Client(transport=httpx.MockTransport(handle))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(oauth.login, 5, on_authorize=urls.put, open_browser=False)
        url = urls.get(timeout=3)
        params = parse_qs(urlparse(url).query)
        assert params["code_challenge_method"] == ["S256"]
        with httpx.Client(trust_env=False) as http:
            callback = f"http://127.0.0.1:{port}/callback"
            assert (
                http.get(callback, params={"state": "wrong", "code": "unused"}).status_code == 400
            )
            assert (
                http.get(
                    callback, params={"state": params["state"][0], "code": "synthetic-code"}
                ).status_code
                == 200
            )
        pending.result(timeout=3)
    browser.assert_not_called()
    assert len(posts) == 1
    assert store.value["access_token"] == "synthetic-access"
    assert "code_verifier" in parse_qs(posts[0].content.decode())
    assert "synthetic-access" not in url and "synthetic-refresh" not in url
    oauth.close()


def test_login_port_conflict_is_safe():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        oauth = OAuth("https://service.example", Store())
        oauth._config = {
            "redirect_uri": f"http://127.0.0.1:{sock.getsockname()[1]}/callback",
            "client_id": "c",
            "scope": "openid",
            "issuer": "https://identity.example",
        }
        with pytest.raises(ClientError, match="端口不可用"):
            oauth.login(1)
        oauth.close()


def test_cli_login_exposes_pending_link_before_wait(monkeypatch, capsys):
    def login(timeout, *, on_authorize, open_browser):
        assert timeout == 60 and open_browser is False
        on_authorize("https://identity.example/authorize?state=public-state")
        assert "authorization_pending" in capsys.readouterr().out
        return {"name": "员工"}

    client = SimpleNamespace(url="https://service.example", login=login)

    class Context:
        def __enter__(self):
            return client

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(cli, "OfficeClient", lambda _: Context())
    monkeypatch.setattr(
        "sys.argv",
        [
            "office",
            "--service",
            "https://service.example",
            "login",
            "--no-browser",
            "--timeout",
            "60",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["name"] == "员工"


def test_service_url_requires_real_host():
    with pytest.raises(ClientError):
        trusted_url("https://")


@pytest.mark.parametrize("status", [429, 503])
def test_refresh_service_failure_preserves_credentials(status):
    stored = {"access_token": "old", "refresh_token": "old-refresh", "expires_at": 1}
    store = Store(stored)
    store.clear = Mock()
    oauth = OAuth("https://service.example", store)
    oauth._config = {"issuer": "https://identity.example", "client_id": "c"}
    oauth.http.close()
    oauth.http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json={"error": "temporarily_unavailable"})
        )
    )
    with pytest.raises(ClientError, match="凭证已保留"):
        oauth.access_token()
    assert store.value is stored
    store.clear.assert_not_called()
    oauth.close()


def test_refresh_without_rotated_token_keeps_original_refresh():
    store = Store({"access_token": "old", "refresh_token": "old-refresh", "expires_at": 1})
    oauth = OAuth("https://service.example", store)
    oauth._config = {"issuer": "https://identity.example", "client_id": "c"}
    oauth.http.close()
    oauth.http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"access_token": "new", "expires_in": 300})
        )
    )
    assert oauth.access_token() == "new"
    assert store.value["refresh_token"] == "old-refresh"
    oauth.close()


@pytest.mark.parametrize("tokens", [{}, {"access_token": "new", "expires_in": float("inf")}])
def test_invalid_token_response_does_not_overwrite_store(tokens):
    store = Store({"sentinel": "existing"})
    oauth = OAuth("https://service.example", store)
    with pytest.raises(ClientError):
        oauth._save(tokens)
    assert store.value == {"sentinel": "existing"}
    oauth.close()


def test_invalid_discovery_is_not_cached():
    oauth = OAuth("https://service.example", Store())
    oauth.http.close()
    count = []

    def handle(request):
        count.append(request)
        return httpx.Response(
            200,
            json={
                "issuer": "http://untrusted.example",
                "client_id": "c",
                "scope": "openid",
                "redirect_uri": "http://127.0.0.1:8765/callback",
            },
        )

    oauth.http = httpx.Client(transport=httpx.MockTransport(handle))
    for _ in range(2):
        with pytest.raises(ClientError):
            oauth.config()
        assert oauth._config is None
    assert len(count) == 2
    oauth.close()
