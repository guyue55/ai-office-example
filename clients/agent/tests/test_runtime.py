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
                    "prepare_data",
                    {"change": {"operation": "project.create", "payload": {"name": "项目A"}}},
                    tool_call_id="p",
                )
            ]
        )
    if results[-1].tool_name == "prepare_data":
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
            parts=[ToolCallPart("read_projects", {}, tool_call_id=str(len(messages)))]
        )

    result = asyncio.run(run_task(FunctionModel(loop), Client, "查询", limits=Limits(requests=2)))
    assert result.status == "budget_exceeded"
    assert result.usage["requests"] == 2


def test_revocation_stops_next_model_request():
    client = Client()
    requests = []

    def model(messages, info):
        requests.append(True)
        return ModelResponse(parts=[ToolCallPart("read_projects", {}, tool_call_id="read")])

    original = client.projects

    def revoke(**params):
        result = original(**params)
        client.identity["permissions"] = []
        return result

    client.projects = revoke
    result = asyncio.run(run_task(FunctionModel(model), lambda: client, "列出项目"))
    assert result.status == "failed" and "权限" in result.explanation
    assert len(requests) == 1  # 撤权后不再发送下一轮模型请求。


def test_bad_tool_and_raw_exception_do_not_leak(caplog):
    def malicious(messages, info):
        if not last_tool(messages):
            return ModelResponse(
                parts=[ToolCallPart("read_projects", {"unknown": "x"}, tool_call_id="x")]
            )
        return ModelResponse(parts=[TextPart("参数不合规，未执行查询。")])

    result = asyncio.run(run_task(FunctionModel(malicious), Client, "读取"))
    assert result.status == "completed"
    assert not result.receipts
    assert "参数不合规" in result.explanation

    def broken(messages, info):
        raise RuntimeError("Bearer super-secret https://secret.example")

    result = asyncio.run(run_task(FunctionModel(broken), Client, "读取"))
    assert result.status == "failed"
    assert "super-secret" not in json.dumps(result.as_dict())
    assert "super-secret" not in caplog.text
    assert "secret.example" not in caplog.text


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


def test_invalid_arguments_return_hint_and_model_can_correct():
    def model(messages, info):
        results = last_tool(messages)
        if not results:
            return ModelResponse(
                parts=[ToolCallPart("read_projects", {"unknown": 1}, tool_call_id="bad")]
            )
        if results[-1].content.get("status") == "invalid_arguments":
            return ModelResponse(parts=[ToolCallPart("read_projects", {}, tool_call_id="ok")])
        return ModelResponse(parts=[TextPart("已查询，覆盖范围以回执为准。")])

    result = asyncio.run(run_task(FunctionModel(model), Client, "查询容器"))
    assert result.status == "completed"
    assert [item["operation"] for item in result.receipts] == ["projects"]
    assert result.usage["requests"] == 3


def test_missing_required_arguments_never_abort_the_run():
    def model(messages, info):
        results = last_tool(messages)
        if not results:
            return ModelResponse(parts=[ToolCallPart("read_schema", {}, tool_call_id="bad")])
        return ModelResponse(parts=[TextPart("缺少项目参数，请补充后我再查询。")])

    result = asyncio.run(run_task(FunctionModel(model), Client, "查询结构"))
    assert result.status == "completed"
    assert not result.receipts
    assert result.usage["requests"] == 2
    assert "缺少项目参数" in result.explanation


def test_hidden_write_tool_is_discoverable_by_search():
    calls = []

    def model(messages, info):
        calls.append(len(calls))
        if len(calls) == 1:
            # 查询意图不预载变更工具，先得到可纠正提示。
            return ModelResponse(
                parts=[ToolCallPart("prepare_data", {"change": {}}, tool_call_id="hidden")]
            )
        if len(calls) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart("search_tools", {"queries": ["新建项目"]}, tool_call_id="search")
                ]
            )
        if len(calls) == 3:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "prepare_data",
                        {"change": {"operation": "project.create", "payload": {"name": "项目"}}},
                        tool_call_id="ok",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("已生成预览，等待确认。")])

    result = asyncio.run(
        run_task(
            FunctionModel(model),
            Client,
            "记录一下",
            approve=lambda proposal: False,
        )
    )
    assert result.status == "completed"
    assert not result.receipts
    assert result.usage["requests"] == 4
