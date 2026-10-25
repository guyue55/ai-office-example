"""模型循环、累计预算、拒绝和失败的确定性验证，不声称真实模型质量。"""

import asyncio
import json

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from test_gateway import Client

from office_agent.config import Limits
from office_agent.runtime import run_task


def last_tool(messages):
    return [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]


def write_model(messages, info):
    results = last_tool(messages)
    if not results:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "prepare_write",
                    {
                        "operation": "data",
                        "arguments": {
                            "change": {"operation": "project.create", "payload": {"name": "项目A"}}
                        },
                    },
                    tool_call_id="p",
                )
            ]
        )
    if results[-1].tool_name == "prepare_write":
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "execute_write", {"ticket": results[-1].content["ticket"]}, tool_call_id="w"
                )
            ]
        )
    return ModelResponse(parts=[TextPart("请以工具回执为准。")])


def test_approval_is_outside_model_and_shows_exact_content():
    client = Client()
    seen = []

    async def approve(proposal):
        assert not client.calls
        seen.append(proposal.arguments["change"]["payload"]["name"])
        return True

    result = asyncio.run(
        run_task(FunctionModel(write_model), lambda: client, "创建项目", approve=approve)
    )
    assert result.status == "completed"
    assert seen == ["项目A"] and len(client.calls) == 1
    assert result.usage["requests"] == 3
    assert result.receipts[-1]["status"] == "received"
    assert client.closed


def test_default_and_explicit_denial_never_write():
    for approval in [None, deny]:
        client = Client()
        result = asyncio.run(
            run_task(
                FunctionModel(write_model),
                lambda client=client: client,
                "创建项目",
                approve=approval,
            )
        )
        assert result.status == "completed"
        assert not client.calls and client.closed
        assert result.receipts[-1]["status"] == "denied"


async def deny(_):
    return False


def test_budget_carries_across_approval_resume():
    client = Client()

    async def approve(_):
        return True

    result = asyncio.run(
        run_task(
            FunctionModel(write_model),
            lambda: client,
            "创建项目",
            limits=Limits(requests=2),
            approve=approve,
        )
    )
    assert result.status == "budget_exceeded"
    assert len(client.calls) == 1  # 已成功的写入不因总结预算不足而重放。
    assert result.usage["requests"] == 2


def test_loop_is_bounded():
    def loop(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "read_office",
                    {"operation": "projects", "arguments": {}},
                    tool_call_id=str(len(messages)),
                )
            ]
        )

    result = asyncio.run(run_task(FunctionModel(loop), Client, "查询", limits=Limits(requests=2)))
    assert result.status == "budget_exceeded"
    assert result.usage["requests"] == 2


def test_bad_tool_and_raw_exception_do_not_leak():
    def malicious(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "read_office", {"operation": "request", "arguments": {}}, tool_call_id="x"
                )
            ]
        )

    result = asyncio.run(run_task(FunctionModel(malicious), Client, "读取"))
    assert result.status == "failed"
    assert "未注册" in result.explanation

    def broken(messages, info):
        raise RuntimeError("Bearer super-secret https://secret.example")

    result = asyncio.run(run_task(FunctionModel(broken), Client, "读取"))
    assert result.status == "failed"
    assert "super-secret" not in json.dumps(result.as_dict())


def test_timeout_and_cancellation_close_client():
    async def slow(messages, info):
        await asyncio.sleep(10)
        return ModelResponse(parts=[TextPart("late")])

    client = Client()
    result = asyncio.run(
        run_task(FunctionModel(slow), lambda: client, "等待", limits=Limits(seconds=1))
    )
    assert result.status == "timeout" and client.closed

    async def cancelled():
        other = Client()
        task = asyncio.create_task(run_task(FunctionModel(slow), lambda: other, "取消"))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("取消未传播")
        assert other.closed

    asyncio.run(cancelled())
