"""连接检查无凭证副作用；缺失配置不能连接隐含localhost。"""

import json
import sys
from unittest.mock import Mock

import httpx
import pytest
from office_client import cli, connection
from office_client.auth import ClientError, KeyringStore
from office_client.product import PRODUCT


@pytest.fixture
def server(monkeypatch):
    original = connection.OAuth
    for name in ("load", "save", "clear"):
        monkeypatch.setattr(KeyringStore, name, Mock(side_effect=AssertionError("禁止凭证IO")))

    def configure(handler):
        def factory(*args, **kwargs):
            oauth = original(*args, **kwargs)
            oauth.http.close()
            oauth.http = httpx.Client(transport=httpx.MockTransport(handler))
            return oauth

        monkeypatch.setattr(connection, "OAuth", factory)

    return configure


def reply(request):
    base = "https://office.example"
    if request.url.path == "/health/ready":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/auth/config":
        return httpx.Response(
            200,
            json={
                "issuer": base + "/identity",
                "client_id": PRODUCT["client_id"],
                "redirect_uri": "http://127.0.0.1:8765/callback",
                "scope": "openid office",
            },
        )
    return httpx.Response(200, json={"issuer": base + "/identity"})


def test_anonymous_connection_check_does_not_read_credentials(server):
    server(reply)
    result = connection.check_connection("https://office.example")
    assert result["ok"] and result["online_verified"]
    assert not result["identity_verified"] and not result["credential_io"]


@pytest.mark.parametrize("status", [301, 401, 404, 503])
def test_connection_failure_never_follows_or_falls_back(server, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://elsewhere.example"})

    server(handler)
    with pytest.raises(ClientError):
        connection.check_connection("https://office.example")
    assert len(calls) == 1
    assert all(item.url.host == "office.example" for item in calls)


def test_invalid_discovery_rejected(server):
    def handler(request):
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": "https://wrong.example"})
        return reply(request)

    server(handler)
    with pytest.raises(ClientError, match="不一致"):
        connection.check_connection("https://office.example")


def test_unconfigured_cli_asks_for_address_before_network(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["office", "whoami"])
    client = Mock(side_effect=AssertionError("不应访问默认地址"))
    monkeypatch.setattr(cli, "OfficeClient", client)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "尚未配置服务端地址" in capsys.readouterr().err
    client.assert_not_called()


def test_connection_check_cli_returns_machine_result(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["office", "--service", "https://office.example", "connection-check"]
    )
    monkeypatch.setattr(cli, "check_connection", lambda *args, **kwargs: {"ok": True})
    cli.main()
    assert json.loads(capsys.readouterr().out)["ok"]


def test_failed_connection_check_cli_returns_structured_safe_issue(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["office", "--service", "https://office.example", "connection-check"]
    )

    def fail(*args, **kwargs):
        raise connection.ConnectionCheckError(
            "服务尚未就绪", code="service_not_ready", stage="service_ready", status=503
        )

    monkeypatch.setattr(cli, "check_connection", fail)
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["issue"]["http_status"] == 503 and output["credential_io"] is False


def test_invalid_address_reports_configuration_stage_without_credential_io():
    with pytest.raises(connection.ConnectionCheckError) as caught:
        connection.check_connection("http://8.8.8.8")
    assert caught.value.code == "invalid_address" and caught.value.stage == "configuration"
