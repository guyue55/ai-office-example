"""实际运行过程、增量文字及审批语义；仅使用确定性模型。"""

import asyncio

from pydantic_ai.models.function import FunctionModel
from test_gateway import Client
from test_runtime import write_model

from office_agent.runtime import run_task


def test_streamed_text_is_observed_before_completion_without_thinking():
    async def scenario():
        seen = []
        release = asyncio.Event()

        async def stream(messages, info):
            yield "先查询权限。"
            await release.wait()
            yield "再整理可执行操作。"

        async def observe(value):
            seen.append(value)
            if value.get("text"):
                release.set()

        result = await asyncio.wait_for(
            run_task(FunctionModel(stream_function=stream), Client, "查询权限", observe=observe),
            timeout=3,
        )
        assert result.status == "completed"
        assert any("先查询权限" in value.get("text", "") for value in seen)
        assert seen[-1]["stage"] == "completed"
        assert [v["seq"] for v in seen] == sorted({v["seq"] for v in seen})
        assert all("thinking" not in value for value in seen)

    asyncio.run(scenario())


def test_progress_preserves_approval_and_observer_failure_cannot_replay_write():
    async def scenario():
        client, seen = Client(), []

        async def observe(value):
            seen.append(value)
            if value["stage"] == "tool":
                raise RuntimeError("展示通道不可用")

        async def approve(proposal):
            assert not client.calls
            return True

        result = await run_task(
            FunctionModel(write_model), lambda: client, "创建项目", approve=approve, observe=observe
        )
        assert result.status == "completed"
        assert len(client.calls) == 1
        assert any(value["stage"] == "tool" for value in seen)

    asyncio.run(scenario())


def test_text_before_tool_does_not_end_run_or_skip_actual_read():
    from pydantic_ai.models.function import DeltaToolCall

    async def scenario():
        seen = []

        async def stream(messages, info):
            from test_runtime import last_tool

            if not last_tool(messages):
                yield "先读取数据。"
                yield {
                    0: DeltaToolCall(
                        name="read_projects",
                        json_args="{}",
                        tool_call_id="read-test",
                    )
                }
            else:
                yield "已读取，仍有下一页。"

        async def observe(value):
            seen.append(value)

        result = await run_task(
            FunctionModel(stream_function=stream), Client, "查询数据", observe=observe
        )
        assert result.status == "completed"
        assert result.usage["requests"] == 2
        assert result.receipts[0]["operation"] == "projects"
        assert result.receipts[0]["result"]["next"] == "more"
        assert any(
            step["operation"] == "projects" and step["status"] == "completed"
            for item in seen
            for step in item["steps"]
        )

    asyncio.run(scenario())


def test_stream_projection_excludes_raw_thinking_and_masks_split_bearer():
    from pydantic_ai.messages import (
        PartDeltaEvent,
        PartStartEvent,
        TextPart,
        TextPartDelta,
        ThinkingPart,
    )

    from office_agent.progress import Progress

    async def scenario():
        seen = []

        async def observe(value):
            seen.append(value)

        async def events():
            yield PartStartEvent(
                index=0, part=ThinkingPart("private-reasoning", signature="private-signature")
            )
            yield PartStartEvent(index=1, part=TextPart("说明 Bear"))
            yield PartDeltaEvent(index=1, delta=TextPartDelta(content_delta="er secret-"))
            yield PartDeltaEvent(index=1, delta=TextPartDelta(content_delta="sentinel 完成"))

        progress = Progress(observe)
        await progress.stream(None, events())
        await progress.flush()
        serialized = str(seen)
        assert "private-reasoning" not in serialized and "private-signature" not in serialized
        assert "secret-" not in serialized
        assert "Bearer [已隐藏]" in seen[-1]["text"]

    asyncio.run(scenario())


def test_unregistered_tool_name_is_not_echoed_into_progress():
    from office_agent.progress import Progress

    async def scenario():
        seen = []

        async def observe(value):
            seen.append(value)

        async def reject():
            raise ValueError("invalid")

        try:
            progress = Progress(observe)
            await progress.call("Bearer private-operation", reject)
        except ValueError:
            pass
        await progress.flush()
        assert "private-operation" not in str(seen)

    asyncio.run(scenario())


def test_projection_has_byte_and_step_budgets_after_sanitization():
    from pydantic_ai.messages import PartStartEvent, TextPart

    from office_agent.progress import Progress

    async def scenario():
        seen = []

        async def observe(value):
            seen.append(value)

        async def events():
            yield PartStartEvent(index=0, part=TextPart("Bearer a " * 10))

        progress = Progress(observe, text_limit=128)
        await progress.stream(None, events())
        await progress.flush()
        assert len(seen[-1]["text"].encode()) <= 128
        assert seen[-1]["truncated"]

        async def execute():
            return None

        for _ in range(70):
            await progress.call("describe_operations", execute)
        await progress.flush()
        assert len(seen[-1]["steps"]) == 64
        assert not seen[0]["steps"]  # 旧快照不会被后续可变列表污染。

    asyncio.run(scenario())


def test_slow_observer_cannot_block_stream_consumption_and_latest_is_flushed():
    from pydantic_ai.messages import PartStartEvent, TextPart

    from office_agent.progress import Progress

    async def scenario():
        consumed = asyncio.Event()
        seen = []

        async def observe(value):
            # 观察端依赖流消费完毕；同步await观察端会形成时序阻塞。
            await asyncio.wait_for(consumed.wait(), timeout=0.2)
            seen.append(value)

        async def events():
            yield PartStartEvent(index=0, part=TextPart("开始"))
            yield PartStartEvent(index=1, part=TextPart("完成"))
            consumed.set()

        progress = Progress(observe)
        await progress.stream(None, events())
        await progress.flush()
        assert consumed.is_set()
        assert seen[-1]["text"] == "开始完成"
        assert progress.observer is not None
        assert progress.pending is None
        assert progress.worker is None or progress.worker.done()

    asyncio.run(scenario())


def test_observer_queue_is_coalesced_and_flush_cancellation_cleans_worker():
    import pytest

    from office_agent.progress import Progress

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        seen = []

        async def observe(value):
            started.set()
            await release.wait()
            seen.append(value)

        progress = Progress(observe)
        await progress.emit("identity")
        await started.wait()
        for _ in range(100):
            await progress.emit("model")
        assert progress.pending["seq"] == 101
        release.set()
        await progress.flush()
        assert [item["seq"] for item in seen] == [1, 101]

        started.clear()
        release.clear()
        await progress.emit("model")
        await started.wait()
        flush = asyncio.create_task(progress.flush())
        await asyncio.sleep(0)
        flush.cancel()
        with pytest.raises(asyncio.CancelledError):
            await flush
        assert progress.worker.done()
        assert progress.pending is None

    asyncio.run(scenario())


def test_fragmented_tool_arguments_finish_before_read_with_slow_observer():
    from pydantic_ai.models.function import DeltaToolCall
    from test_runtime import last_tool

    async def scenario():
        finished = asyncio.Event()

        class CheckedClient(Client):
            def projects(self, **params):
                assert finished.is_set(), "参数流完成前不得执行业务查询"
                return super().projects(**params)

        async def stream(messages, info):
            if not last_tool(messages):
                yield "开始查询。"
                yield {
                    0: DeltaToolCall(name="read_projects", json_args="", tool_call_id="fragmented")
                }
                await asyncio.sleep(0.01)
                yield {0: DeltaToolCall(json_args='{"limit":')}
                await asyncio.sleep(0.01)
                yield {0: DeltaToolCall(json_args="10}")}
                finished.set()
            else:
                yield "已读取，存在下一页。"

        async def observe(value):
            await asyncio.sleep(0.05)

        result = await run_task(
            FunctionModel(stream_function=stream), CheckedClient, "查容器", observe=observe
        )
        assert result.status == "completed"
        assert len(result.receipts) == 1
        assert result.receipts[0]["operation"] == "projects"
        assert result.usage["requests"] == 2

    asyncio.run(scenario())
