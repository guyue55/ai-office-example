"""内部Agent复用同一应用端口；模型依赖未安装时显式跳过，不影响基础服务。"""

import asyncio

import pytest
from pydantic import BaseModel

pytest.importorskip("office_agent", reason="需uv sync --extra agent，独立Agent门另行执行")

from app.application.agent_tools import ApplicationToolBridge
from app.application.commands import validate_command
from app.common.errors import DomainError
from office_agent.config import Limits
from office_agent.gateway import Gateway
from office_agent.policy import ToolFailure


class Output(BaseModel):
    id: str = "test-employee"
    access_revision: int = 1
    permissions: list[str] = ["project.read"]


class Application:
    def __init__(self):
        self.calls = []

    async def call(self, principal, operation, payload, trace_id):
        # 用生产参数校验器验证实际适配，不依据适配器本身推导期望值。
        validate_command(operation, payload)
        self.calls.append((principal, operation, payload))
        return Output().model_dump()


@pytest.mark.parametrize(
    "method,args,operation,expected",
    [
        (
            "schema",
            {"project_id": "project-a", "revision": 2},
            "schema.get",
            {"identifier": "project-a", "revision": 2},
        ),
        ("record", {"record_id": "record-a"}, "record.get", {"identifier": "record-a"}),
        (
            "confirm",
            {"record_id": "record-a", "version": 2},
            "record.confirm",
            {"identifier": "record-a", "payload": {"version": 2}},
        ),
        (
            "export_confirmed",
            {"project_id": "project-a", "max_records": 10},
            "record.export.confirmed",
            {"identifier": "project-a", "max_records": 10},
        ),
        ("projects", {"limit": 10}, "project.list", {"limit": 10}),
    ],
)
def test_native_payloads_and_current_principal(method, args, operation, expected):
    app = Application()
    issued = []

    async def authenticate():
        value = object()
        issued.append(value)
        return value

    async def scenario():
        bridge = ApplicationToolBridge(app, authenticate)
        await bridge.call(method, **args)
        await bridge.call(method, **args)
        assert app.calls[0][1:] == (operation, expected)
        assert app.calls[0][0] is issued[0] and app.calls[1][0] is issued[1]

    asyncio.run(scenario())


def test_revocation_is_not_bypassed_and_unknown_tools_do_not_authenticate():
    app = Application()

    async def authenticate():
        raise DomainError(401, "invalid_token", "secret-body")

    async def scenario():
        bridge = ApplicationToolBridge(app, authenticate)
        with pytest.raises(ToolFailure) as error:
            await bridge.call("projects", limit=10)
        assert "secret-body" not in str(error.value) and not error.value.outcome_unknown
        with pytest.raises(ToolFailure, match="未注册"):
            await bridge.call("request", path="/arbitrary")
        assert not app.calls

    asyncio.run(scenario())


def test_shared_gateway_accepts_native_async_bridge():
    app = Application()

    async def authenticate():
        return "test-principal"

    async def scenario():
        gateway = Gateway(ApplicationToolBridge(app, authenticate), Limits())
        await gateway.start()
        await gateway.read("projects", {})
        assert [row[1] for row in app.calls] == ["me", "me", "project.list"]

    asyncio.run(scenario())


def test_definitive_domain_rejection_clears_write_observation():
    class Denied:
        async def call(self, *args):
            raise DomainError(403, "permission_denied", "private-message")

    async def scenario():
        events = []

        async def authenticate():
            return "principal"

        async def observe(method, phase, result):
            events.append((method, phase, result))

        bridge = ApplicationToolBridge(Denied(), authenticate, observer=observe)
        with pytest.raises(ToolFailure) as failure:
            await bridge.call("confirm", record_id="r", version=1)
        assert not failure.value.outcome_unknown
        assert [phase for _, phase, _ in events] == ["before", "rejected"]
        assert events[-1][2] == {"status": 403, "code": "permission_denied"}

    asyncio.run(scenario())
