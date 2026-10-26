"""输出边界与不自动重放的客户端恢复契约。"""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.application.contracts import validate_output
from app.application.service import Application
from app.common.errors import DomainError
from office_client import OfficeClient, ServiceError


@pytest.mark.parametrize(
    "result",
    [
        {"id": "p", "version": 1, "name": "项目", "archived": False},
        {
            "id": "p",
            "version": 1,
            "name": "项目",
            "archived": False,
            "schema_revision": 0,
            "internal_secret": "private",
        },
        {"id": "p", "version": "1", "name": "项目", "archived": False, "schema_revision": 0},
    ],
)
def test_invalid_output_is_server_error(result):
    with pytest.raises(DomainError) as caught:
        validate_output("project.create", {}, result)
    assert (caught.value.status, caught.value.code) == (500, "invalid_response")
    assert "private" not in caught.value.message


def test_dynamic_record_output_preserves_fields():
    result = {
        "id": "r",
        "version": 1,
        "project_id": "p",
        "schema_revision": 1,
        "owner_id": "u",
        "values": {"project_specific_metric": "42.00"},
        "archived": False,
        "status": "submitted",
    }
    assert validate_output("record.get", {}, result) == result


def test_operation_failure_trace_omits_input(caplog):
    app = Application(None, None, None)
    app._execute = AsyncMock(side_effect=DomainError(403, "permission_denied", "private-message"))
    caplog.set_level(logging.INFO, logger="office.service")

    async def run():
        with pytest.raises(DomainError):
            await app.call(None, "record.get", {"private": "private-value"}, "trace-test")

    asyncio.run(run())
    assert "permission_denied" in caplog.text and "trace-test" in caplog.text
    assert "private-value" not in caplog.text and "private-message" not in caplog.text
    event = json.loads(caplog.records[-1].getMessage().split("] ", 1)[1])
    assert event["outcome"] == "failed" and event["actor_id"] is None


@pytest.mark.parametrize(
    "method,path,status,action,outcome,recovery,unknown",
    [
        # 服务端恢复元数据优先；动作按契约库枚举映射为客户端提示。
        (
            "GET",
            "/api/v2/projects",
            503,
            "query_operation",
            "unknown",
            "query_before_retry",
            False,
        ),
        (
            "POST",
            "/api/v2/records",
            503,
            "replay_same_key",
            "unknown",
            "replay_same_key",
            True,
        ),
        (
            "PATCH",
            "/api/v2/projects/p",
            503,
            "query_operation",
            "unknown",
            "query_before_retry",
            True,
        ),
        (
            "PATCH",
            "/api/v2/projects/p",
            409,
            "refresh",
            "not_applied",
            "refresh_and_correct",
            False,
        ),
        ("GET", "/api/v2/projects", 401, "reauthenticate", "not_applied", "login", False),
        (
            "GET",
            "/api/v2/projects",
            403,
            "check_permissions",
            "not_applied",
            "check_permissions",
            False,
        ),
        # 无恢复元数据（如旧网关）回退到纯状态判定。
        ("PATCH", "/api/v2/projects/p", 409, None, None, "refresh_and_correct", True),
        # 未知动作保守提示联系管理员。
        ("GET", "/api/v2/projects", 503, "contact_admin", "not_applied", "check_service", False),
    ],
)
def test_client_response_recovery_never_replays(
    method, path, status, action, outcome, recovery, unknown
):
    calls = []
    meta = {"trace_id": "trace-remote", "contract_version": "1"}
    if action is not None:
        meta = {
            "trace_id": "trace-remote",
            "contract_version": "1",
            "recovery": {"action": action, "write_outcome": outcome},
        }

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            # v2 失败体带恢复元数据；动作与结果状态由服务端契约统一声明。
            json={
                "code": {401: 401001, 403: 403001, 409: 409001, 503: 503002}[status],
                "message": "受控失败",
                "data": None,
                "meta": meta,
            },
            headers={"X-Trace-ID": "trace-remote"},
        )

    with OfficeClient("http://127.0.0.1:8800", store=SimpleNamespace()) as client:
        client.oauth.access_token = lambda: "synthetic"
        client.http.close()
        client.http = httpx.Client(base_url=client.url, transport=httpx.MockTransport(handle))
        with pytest.raises(ServiceError) as caught:
            client.request(method, path)
        assert len(calls) == 1
        assert caught.value.trace_id == "trace-remote"
        assert caught.value.recovery == recovery
        assert caught.value.outcome_unknown == unknown


@pytest.mark.parametrize(
    "failure,unknown", [(httpx.ConnectTimeout, False), (httpx.ReadTimeout, True)]
)
def test_write_network_failure_requires_explicit_recovery(failure, unknown):
    calls = []

    def handle(request):
        calls.append(1)
        raise failure("private-transport-detail", request=request)

    with OfficeClient("http://127.0.0.1:8800", store=SimpleNamespace()) as client:
        client.oauth.access_token = lambda: "synthetic"
        client.http.close()
        client.http = httpx.Client(base_url=client.url, transport=httpx.MockTransport(handle))
        with pytest.raises(ServiceError) as caught:
            client.request("POST", "/api/v2/records", json={"idempotency_key": "original"})
        assert calls == [1] and caught.value.outcome_unknown == unknown
        assert caught.value.recovery == "replay_same_key"
        assert "private-transport-detail" not in str(caught.value)


def test_all_business_http_routes_publish_output_models():
    from app.application.codebook import build_registry
    from app.entrypoints.http.routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router(SimpleNamespace(), registry=build_registry()))
    schema = app.openapi()
    operations = [
        operation
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "patch", "put", "delete"}
    ]
    assert len(operations) == 51  # 新增本人会话创建、列表、详情及归档四个入口。
    assert "post" in schema["paths"]["/api/v2/data/aggregate"]
    assert all(
        operation["responses"][
            next(code for code in operation["responses"] if code.startswith("2"))
        ]["content"]["application/json"]["schema"]
        for operation in operations
    )
    # v2 成功体统一包装；业务模型以 Success[RecordOutput] 等生成名称进入组件。
    record_values_schemas = [
        name
        for name, component in schema["components"]["schemas"].items()
        if "values" in component.get("properties", {})
        and component["properties"]["values"].get("type") == "object"
    ]
    assert any("RecordOutput" in name for name in record_values_schemas), sorted(
        schema["components"]["schemas"]
    )


def test_recovery_message_explains_uncertain_write():
    error = ServiceError(
        "请求超时",
        code="transport_error",
        trace_id="trace-visible",
        recovery="replay_same_key",
        outcome_unknown=True,
    )
    assert "原幂等键" in error.user_message()
    assert "尚未确认" in error.user_message()
    assert "trace-visible" in error.user_message()
