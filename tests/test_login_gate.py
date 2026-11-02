"""登录保护不按员工或设备限制；共享出口正常突发与旧会话均保持可用。"""

import time
from types import SimpleNamespace

import pytest
import test_web_console
from app.common.errors import DomainError
from app.entrypoints.web.login_gate import LoginGate
from app.entrypoints.web.responses import instance_cookie
from app.infrastructure.config import LoginProtection

console_fixture = test_web_console.web


@pytest.fixture
def web(console_fixture):
    return console_fixture


def test_one_source_cannot_occupy_all_pending_and_other_source_is_available():
    gate = LoginGate(LoginProtection(pending_per_source=4, burst=10))
    pending = {}
    for i in range(4):
        source = gate.admit("10.0.0.1", pending)
        pending[str(i)] = ({"_source": source}, 9999999999)
    with pytest.raises(DomainError) as error:
        gate.admit("10.0.0.1", pending)
    assert error.value.code == "authentication_busy"
    assert gate.admit("10.0.0.2", pending) != source
    pending.pop("0")
    assert gate.admit("10.0.0.1", pending) == source


def test_shared_exit_accepts_normal_simultaneous_browsers():
    gate = LoginGate(LoginProtection())
    pending = {}
    # 同一出口40台设备同时发起，尚未完成的事务不应被默认误限。
    for i in range(40):
        source = gate.admit("10.0.0.1", pending)
        pending[str(i)] = ({"_source": source}, 9999999999)


def test_rate_refill_and_source_storage_are_bounded(monkeypatch):
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr("app.entrypoints.web.login_gate.time.monotonic", lambda: clock.value)
    gate = LoginGate(LoginProtection(burst=2, refill_per_second=1, max_sources=2))
    gate.admit("a", {})
    gate.admit("a", {})
    with pytest.raises(DomainError):
        gate.admit("a", {})
    clock.value += 1
    gate.admit("a", {})
    for source in ("b", "c", "d"):
        gate.admit(source, {})
    assert len(gate.buckets) == 2


def test_spoofed_forwarding_header_cannot_bypass_real_route_gate(web, monkeypatch):
    # 固定限流器时钟，避免慢速CI执行期间自动补充配额造成随机失败。
    now = time.monotonic()
    monkeypatch.setattr(
        "app.entrypoints.web.login_gate.time", SimpleNamespace(monotonic=lambda: now)
    )
    # 两次真实新浏览器发起之间清空Cookie，伪造XFF不得成为新的配额来源。
    for i in range(60):
        web.client.cookies.clear()
        assert (
            web.client.get("/console", headers={"x-forwarded-for": f"10.0.0.{i}"}).status_code
            == 303
        )
    web.client.cookies.clear()
    assert web.client.get("/console", headers={"x-forwarded-for": "192.0.2.1"}).status_code == 503
    web.client.cookies.set(
        instance_cookie("office_web_session", web.application.settings.public_url), web.cookie
    )
    assert web.client.get("/console").headers["location"] == "/console/dashboard"
    assert web.client.get("/console/dashboard").status_code == 200


def test_expired_pending_releases_source_quota(monkeypatch):
    from app.entrypoints.web.session import SessionStore

    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr("app.entrypoints.web.session.time.monotonic", lambda: clock.value)
    gate = LoginGate(LoginProtection(pending_per_source=1, burst=5))
    store = SessionStore(b"isolated-test-secret", 7200)
    source = gate.admit("10.0.0.1", store.pending)
    store.put_pending("isolated-state", {"_source": source})
    with pytest.raises(DomainError):
        gate.admit("10.0.0.1", store.pending)
    clock.value += 601
    store.sweep()
    assert gate.admit("10.0.0.1", store.pending) == source


def test_security_reauthentication_cannot_bypass_shared_gate(web, monkeypatch):
    # 固定限流器时钟，避免慢速CI执行期间自动补充配额造成随机失败。
    now = time.monotonic()
    monkeypatch.setattr(
        "app.entrypoints.web.login_gate.time", SimpleNamespace(monotonic=lambda: now)
    )
    web.application.verifier.verify_async.return_value = SimpleNamespace(
        subject="isolated", claims={}
    )
    for _ in range(60):
        web.client.cookies.clear()
        web.client.cookies.set(
            instance_cookie("office_web_session", web.application.settings.public_url), web.cookie
        )
        assert (
            web.client.post("/console/security/password", data={"csrf": "form-csrf"}).status_code
            == 303
        )
    assert (
        web.client.post("/console/security/password", data={"csrf": "form-csrf"}).status_code == 503
    )
    # 来源登录额度耗尽不撤销已登录员工，不限制其其他业务操作。
    assert web.client.get("/console/dashboard").status_code == 200
