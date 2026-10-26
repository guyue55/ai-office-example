"""同源助手BFF：沿用Web身份／CSRF及统一契约，不接收模型历史或工具决定伪造。"""

from typing import Literal

from fastapi import Query, Request, Response
from office_contracts import Meta, prepare_success
from pydantic import Field

from app.application.codebook import build_registry
from app.application.contracts import OPERATION_OUTPUTS
from app.common.errors import DomainError
from app.common.inputs import Input
from app.common.outputs import AccessPage, Output
from app.modules.agent.outputs import AgentCapability, ConversationSummary


class AssistantBootstrap(Output):
    capability: AgentCapability
    conversations: AccessPage[ConversationSummary]


class AssistantAction(Input):
    action: Literal["create", "start", "decide", "cancel", "archive"]
    csrf: str = Field(min_length=1, max_length=200)
    payload: dict


ACTIONS = {
    "create": "agent.conversation.create",
    "start": "agent.start",
    "decide": "agent.decide",
    "cancel": "agent.cancel",
    "archive": "agent.conversation.archive",
}


def register_assistant_ui(router, operate, render, csrf):
    registry = build_registry()

    def respond(request, result, model, status=200):
        prepared = prepare_success(
            result, model, Meta(trace_id=request.state.trace_id), registry=registry
        )
        return Response(
            prepared.body,
            status_code=status,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/console/assistant/workspace")
    async def workspace(request: Request):
        await operate(request, "agent.capabilities")
        # 能力查询仍核验agent.run：页面不能借未启用状态旁路身份。
        await operate(request, "agent.conversation.list", {"limit": 1})
        return await render(request, "assistant_workspace.html")

    @router.get("/console/assistant-ui/bootstrap")
    async def bootstrap(request: Request, after: str = "", archived: bool = False):
        capability = await operate(request, "agent.capabilities")
        listing = await operate(
            request, "agent.conversation.list", {"limit": 10, "after": after, "archived": archived}
        )
        return respond(
            request, {"capability": capability, "conversations": listing}, AssistantBootstrap
        )

    @router.get("/console/assistant-ui/conversations/{identifier}")
    async def conversation(
        request: Request,
        identifier: str,
        before: str = "",
        limit: int = Query(default=10, ge=1, le=20),
    ):
        result = await operate(
            request,
            "agent.conversation.get",
            {"identifier": identifier, "before": before, "limit": limit},
        )
        return respond(request, result, OPERATION_OUTPUTS["agent.conversation.get"])

    @router.post("/console/assistant-ui/action")
    async def action(request: Request):
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise DomainError(415, "unsupported_media_type", "助手仅接受JSON请求")
        try:
            body = AssistantAction.model_validate(await request.json())
        except ValueError:
            raise DomainError(422, "invalid_input", "助手请求格式无效，请刷新后重试") from None
        csrf(request, {"csrf": body.csrf})
        operation = ACTIONS[body.action]
        result = await operate(request, operation, body.payload)
        return respond(
            request, result, OPERATION_OUTPUTS[operation], 202 if body.action == "start" else 200
        )
