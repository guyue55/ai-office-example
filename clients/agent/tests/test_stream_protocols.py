"""三个SDK协议的SSE线缆验证；不调用真实供应商、不假装推理摘要。"""

import asyncio
import json

import httpx2
import pytest
from test_gateway import Client

from office_agent.config import AgentConfig
from office_agent.providers import configured_model
from office_agent.runtime import run_task


def sse(events):
    return "".join(
        "event: " + item["type"] + "\ndata: " + json.dumps(item, ensure_ascii=False) + "\n\n"
        for item in events
    ).encode()


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
def test_three_protocol_streams_are_observed_with_one_request(api):
    async def scenario():
        calls, seen = [], []

        async def serve(request):
            calls.append(json.loads(request.content))
            assert calls[-1]["stream"] is True
            if api == "openai-completions":
                events = []
                for text, finish in [("你好", None), ("，已完成。", None), ("", "stop")]:
                    events.append(
                        {
                            "id": "test",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "test-model",
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 6,
                                "total_tokens": 16,
                            },
                            "choices": [
                                {"index": 0, "delta": {"content": text}, "finish_reason": finish}
                            ],
                        }
                    )
                data = (
                    "".join(
                        "data: " + json.dumps(item, ensure_ascii=False) + "\n\n" for item in events
                    ).encode()
                    + b"data: [DONE]\n\n"
                )
            elif api == "anthropic-messages":
                data = sse(
                    [
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_test",
                                "type": "message",
                                "role": "assistant",
                                "model": "test-model",
                                "content": [],
                                "stop_reason": None,
                                "usage": {"input_tokens": 10, "output_tokens": 0},
                            },
                        },
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "你好，已完成。"},
                        },
                        {"type": "content_block_stop", "index": 0},
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                            "usage": {"output_tokens": 6},
                        },
                        {"type": "message_stop"},
                    ]
                )
            else:
                message = {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                }
                response = {
                    "id": "resp_test",
                    "object": "response",
                    "created_at": 1,
                    "model": "test-model",
                    "status": "in_progress",
                    "output": [],
                }
                data = sse(
                    [
                        {"type": "response.created", "response": response, "sequence_number": 0},
                        {
                            "type": "response.output_item.added",
                            "output_index": 0,
                            "item": message,
                            "sequence_number": 1,
                        },
                        {
                            "type": "response.content_part.added",
                            "output_index": 0,
                            "content_index": 0,
                            "item_id": "msg_test",
                            "part": {"type": "output_text", "text": "", "annotations": []},
                            "sequence_number": 2,
                        },
                        {
                            "type": "response.output_text.delta",
                            "output_index": 0,
                            "content_index": 0,
                            "item_id": "msg_test",
                            "delta": "你好，已完成。",
                            "sequence_number": 3,
                        },
                        {
                            "type": "response.output_item.done",
                            "output_index": 0,
                            "item": {
                                **message,
                                "status": "completed",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "你好，已完成。",
                                        "annotations": [],
                                    }
                                ],
                            },
                            "sequence_number": 4,
                        },
                        {
                            "type": "response.completed",
                            "response": {
                                **response,
                                "status": "completed",
                                "usage": {
                                    "input_tokens": 10,
                                    "output_tokens": 6,
                                    "total_tokens": 16,
                                },
                            },
                            "sequence_number": 5,
                        },
                    ]
                )
            return httpx2.Response(200, content=data, headers={"content-type": "text/event-stream"})

        config = AgentConfig(
            service_url="https://office.invalid",
            model_url="https://model.invalid/v1",
            model="test-model",
            api=api,
            approved_model_data=True,
        )

        async def observe(value):
            seen.append(value)

        async with configured_model(
            config, key="synthetic", transport=httpx2.MockTransport(serve)
        ) as model:
            result = await run_task(model, Client, "简单问候，不执行修改", observe=observe)
        assert result.status == "completed", result.explanation
        assert "你好" in result.explanation
        assert any("你好" in value["text"] for value in seen)
        assert len(calls) == 1

    asyncio.run(scenario())
