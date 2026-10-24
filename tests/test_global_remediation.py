"""40号复审反例：测试真实公开边界，不以正常路径替代失败验证。"""

import asyncio
import hashlib
import importlib.util
import json
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.common.errors import DomainError
from app.common.pagination import bounded_page
from app.entrypoints.mcp.server import build_mcp
from app.entrypoints.web.session import SessionStore
from app.modules.data.service import Data
from mcp.server.mcpserver.exceptions import ToolError
from office_client import OfficeClient, ServiceError
from office_contracts import TransportFailure, make_registry
from office_contracts.integrations.httpx import request_json


@pytest.mark.parametrize(
    "status,payload",
    [
        (200, {"old": "unwrapped"}),
        (
            200,
            {
                "code": 403001,
                "message": "denied",
                "data": None,
                "meta": {"trace_id": "t", "contract_version": "1"},
            },
        ),
        (502, None),
        (
            503,
            {
                "code": 503001,
                "message": "failure",
                "data": None,
                "meta": {"trace_id": "t", "contract_version": "1", "recovery": {"action": []}},
            },
        ),
    ],
)
def test_sdk_strict_decode_and_uncertain_write(status, payload):
    count = []

    def handle(request):
        count.append(1)
        return (
            httpx.Response(status, json=payload)
            if payload is not None
            else httpx.Response(status, text="private-gateway-detail")
        )

    with OfficeClient("https://service.invalid", store=SimpleNamespace()) as client:
        client.oauth.access_token = lambda: "isolated"
        client.http.close()
        client.http = httpx.Client(base_url=client.url, transport=httpx.MockTransport(handle))
        with pytest.raises(ServiceError) as caught:
            client.request("POST", "/api/v2/records", json={"idempotency_key": "same"})
        assert caught.value.code == 500002 and caught.value.outcome_unknown
        assert count == [1] and "private-gateway-detail" not in str(caught.value)


def test_replay_requires_current_read_permission():
    payload = {
        "project_id": "p",
        "schema_revision": 1,
        "values": {"amount": "100"},
        "idempotency_key": "same",
    }
    digest = hashlib.sha256(
        json.dumps(
            {k: v for k, v in payload.items() if k != "idempotency_key"},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    db = SimpleNamespace(
        refresh=AsyncMock(),
        scalar=AsyncMock(return_value=SimpleNamespace(request_digest=digest)),
        info={},
    )

    def need(permission):
        if permission != "record.create":
            raise DomainError(403, "permission_denied", "读取已撤销")

    access = SimpleNamespace(
        db=db, actor=SimpleNamespace(id="owner"), need=need, allows=lambda _: False
    )

    async def run():
        with pytest.raises(DomainError) as caught:
            await Data(access).create_record(payload)
        assert caught.value.status == 403 and not db.info

    asyncio.run(run())


def test_mcp_error_uses_numeric_code_and_safe_trace():
    app = SimpleNamespace(
        settings=SimpleNamespace(
            issuer="https://identity.invalid", audience="https://service.invalid/mcp"
        )
    )
    server, _ = build_mcp(app)

    async def run():
        with pytest.raises(ToolError) as caught:
            await server.call_tool("list_projects", {"limit": False})
        body = json.loads(str(caught.value))
        assert body["code"] == 422001 and body["meta"]["trace_id"] and body["data"] is None

    asyncio.run(run())


def test_byte_pagination_advances_without_losing_rows():
    rows = [{"id": str(i), "value": "x" * 400000} for i in range(10)]
    remaining = rows
    collected = []
    while remaining:
        page = bounded_page(remaining, lambda row: row, lambda row: row["id"], 50)
        assert page["items"]
        collected.extend(page["items"])
        if page["next"] is None:
            break
        assert page["next"] == page["items"][-1]["id"]
        remaining = rows[int(page["next"]) + 1 :]
    assert collected == rows


def test_session_capacity_and_expiry_are_separate(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.entrypoints.web.session.time.monotonic", lambda: clock[0])
    store = SessionStore(b"isolated", 100, 1, 2)
    cookie = store.create({"access_token": "isolated"})
    for state in ("one", "two"):
        store.put_pending(state, {})
    with pytest.raises(DomainError):
        store.put_pending("three", {})
    with pytest.raises(DomainError):
        store.create({})
    assert store.load(cookie)["access_token"] == "isolated"
    clock[0] = 601
    store.put_pending("three", {})
    assert list(store.pending) == ["three"] and not store.sessions


def test_repeat_prepare_preserves_config_and_creates_web_secret(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "isolated_stack", Path(__file__).resolve().parents[1] / "scripts/dev_stack.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "RUNTIME", tmp_path)
    module.prepare()
    config = tmp_path / "foundation/config.json"
    value = json.loads(config.read_text())
    value["operation_timeout"] = 37
    config.write_text(json.dumps(value))
    before = (tmp_path / "secrets/web_session_secret").read_bytes()
    module.prepare()
    assert json.loads(config.read_text())["operation_timeout"] == 37
    assert (tmp_path / "secrets/web_session_secret").read_bytes() == before
    assert json.loads(config.read_text())["web_session_secret_file"]
    assert (tmp_path / "secrets/web_session_secret").stat().st_mode & 0o077 == 0


def test_sync_deadline_interrupts_caller_without_retry():
    release, closed = Event(), Event()

    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            release.wait(2)
            yield b"{}"

        def close(self):
            closed.set()

    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=Slow()))
    ) as client:
        started = time.monotonic()
        try:
            with pytest.raises(TransportFailure) as caught:
                request_json(
                    client,
                    "POST",
                    "https://isolated.invalid",
                    dict,
                    registry=make_registry("probe"),
                    total_timeout=0.03,
                )
            assert time.monotonic() - started < 0.3 and caught.value.write_outcome == "unknown"
        finally:
            release.set()
            assert closed.wait(1)


def test_sync_deadline_capacity_does_not_spawn_unbounded_workers(monkeypatch):
    from threading import BoundedSemaphore

    from office_contracts.integrations.sync_deadline import within_deadline

    monkeypatch.setattr("office_contracts.integrations.sync_deadline._SLOTS", BoundedSemaphore(2))
    release = Event()
    closed = [Event(), Event()]

    def blocked(index):
        def work(cancelled):
            try:
                release.wait(2)
            finally:
                closed[index].set()

        return work

    try:
        for index in range(2):
            with pytest.raises(TransportFailure):
                within_deadline(blocked(index), 0.02, writing=True)
        with pytest.raises(TransportFailure) as caught:
            within_deadline(lambda _: pytest.fail("容量满时不能开始请求"), 1, writing=True)
        assert caught.value.write_outcome == "not_applied"
    finally:
        release.set()
        assert all(event.wait(1) for event in closed)


def test_invalid_existing_config_is_not_overwritten(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "config_preservation", Path(__file__).resolve().parents[1] / "scripts/dev_stack.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / "config.json"
    for content in (b"{broken", b"[]"):
        config.write_bytes(content)
        with pytest.raises((ValueError, TypeError)):
            module.preserve_config(config, {"example": "new"})
        assert config.read_bytes() == content
