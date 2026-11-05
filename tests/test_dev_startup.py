"""开发环境等待覆盖实际HTTP耗时，保持产品业务超时与认证边界。"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest


def stack(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/dev_stack.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("startup_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wait_budget_includes_http_time(monkeypatch):
    module = stack(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def timeout(url, *, timeout):
        clock[0] += timeout
        raise httpx.ReadTimeout("构造超时")

    get = Mock(side_effect=timeout)
    monkeypatch.setattr(module.httpx, "get", get)
    with pytest.raises(RuntimeError, match="未就绪"):
        module.wait_for("http://127.0.0.1", seconds=3)
    assert clock[0] == 3 and get.call_count == 1


def test_ready_stops_without_sleep(monkeypatch):
    module = stack(monkeypatch)
    monkeypatch.setattr(module.httpx, "get", Mock(return_value=SimpleNamespace(is_success=True)))
    sleep = Mock(side_effect=AssertionError("就绪后不应继续等待"))
    monkeypatch.setattr(module.time, "sleep", sleep)
    module.wait_for("http://127.0.0.1")
    sleep.assert_not_called()


@pytest.mark.parametrize(
    "unsafe", ["https://example.com/realms/office", "http://127.0.0.1/realms/other"]
)
def test_introspection_rejects_non_development_target(monkeypatch, tmp_path, unsafe):
    import json

    stack(monkeypatch)
    import configure_token_introspection as module

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"issuer": unsafe}))
    monkeypatch.setattr(module.httpx, "Client", Mock(side_effect=AssertionError("不得连接")))
    with pytest.raises(RuntimeError, match="本机"):
        module.configure(config)


def test_introspection_rebuild_and_repeat_preserve_remote_secret(monkeypatch, tmp_path):
    import json

    stack(monkeypatch)
    import configure_token_introspection as module

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"issuer": "http://127.0.0.1:18081/realms/office", "audience": "local-audience"})
    )
    clients = []
    requests = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "synthetic-admin"})
        if path.endswith("/introspect"):
            return httpx.Response(200, json={"active": False})
        if path.endswith("/client-secret"):
            return httpx.Response(200, json={"value": "synthetic-secret"})
        if request.method == "POST":
            clients.append({"id": "one", **json.loads(request.content)})
            return httpx.Response(201)
        return httpx.Response(200, json=clients)

    original = httpx.Client
    monkeypatch.setattr(module, "secret", lambda name: "synthetic-password")
    monkeypatch.setattr(
        module.httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(handle))
    )
    module.configure(config)
    first = config.stat().st_mtime_ns
    secret_path = config.with_name("config.introspection.secret")
    secret_time = secret_path.stat().st_mtime_ns
    module.configure(config)
    assert len(clients) == 1
    assert config.stat().st_mtime_ns == first
    assert secret_path.stat().st_mtime_ns == secret_time
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert not any(r.method == "POST" and r.url.path.endswith("/client-secret") for r in requests)
    clients[0]["publicClient"] = True
    with pytest.raises(RuntimeError, match="最小权限"):
        module.configure(config)
    assert secret_path.stat().st_mtime_ns == secret_time
