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
