"""SDK配置边界；不连接真实供应商，不让宿主环境打开内容遥测。"""

import asyncio

import anthropic
import pytest
from pydantic_ai.models.anthropic import AnthropicModel

from office_agent.config import AgentConfig
from office_agent.providers import configured_model


def test_anthropic_disables_environment_telemetry_and_closes_client(monkeypatch):
    monkeypatch.setenv("PROVIDER_TEST_KEY", "isolated-model-key")
    monkeypatch.setenv("ANTHROPIC_OPEN_TELEMETRY", "true")
    observed = []
    factory = anthropic.AsyncAnthropic

    def client(**kwargs):
        assert kwargs["open_telemetry"] == {"traces": False, "propagation": False}
        assert kwargs["timeout"] == 7
        assert kwargs["max_retries"] == 0
        value = factory(**kwargs)
        observed.append(value)
        return value

    monkeypatch.setattr(anthropic, "AsyncAnthropic", client)
    config = AgentConfig(
        service_url="https://office.example",
        model_url="https://model.invalid",
        model="configured-alias",
        api="anthropic-messages",
        api_key_env="PROVIDER_TEST_KEY",
        approved_model_data=True,
        request_timeout=10,
        limits={"seconds": 7},
    )

    async def scenario():
        async with configured_model(config) as model:
            assert isinstance(model, AnthropicModel)

    asyncio.run(scenario())
    assert len(observed) == 1 and observed[0].is_closed()


def test_managed_key_injection_does_not_read_host_key(monkeypatch):
    from office_agent import providers

    observed = []
    factory = providers.AsyncOpenAI

    def client(**kwargs):
        observed.append(kwargs["api_key"])
        return factory(**kwargs)

    def forbidden(_config):
        raise AssertionError("托管密钥不应读取宿主秘密引用")

    monkeypatch.setattr(providers, "AsyncOpenAI", client)
    monkeypatch.setattr(providers, "model_key", forbidden)
    config = AgentConfig(
        service_url="https://office.example",
        model_url="https://model.invalid/v1",
        model="fixture",
        approved_model_data=True,
    )

    async def scenario():
        async with providers.configured_model(config, key="synthetic-managed-key"):
            pass

    asyncio.run(scenario())
    assert observed == ["synthetic-managed-key"]


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
@pytest.mark.parametrize("mode", ["none", "custom-header"])
def test_no_auth_and_gateway_parameters_reach_wire_without_host_credentials(monkeypatch, api, mode):
    import httpx2
    from pydantic_ai import Agent

    monkeypatch.setenv("OPENAI_API_KEY", "host-secret-never-send")
    seen = []

    async def serve(request):
        seen.append(request)
        if api == "anthropic-messages":
            return httpx2.Response(
                200,
                json={
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": "fixture",
                    "content": [{"type": "text", "text": "OK"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        if api == "openai-responses":
            return httpx2.Response(
                200,
                json={
                    "id": "resp_fixture",
                    "object": "response",
                    "created_at": 1,
                    "model": "fixture",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "id": "msg_1",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "OK", "annotations": []}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            )
        return httpx2.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OK"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    config = AgentConfig(
        service_url="https://office.example",
        model_url="http://192.168.1.2/v1",
        allow_model_http=True,
        model="fixture",
        approved_model_data=True,
        api=api,
        auth_mode=mode,
        auth_header="x-gateway-key",
        extra_headers={"x-route": "blue"},
        extra_query={"api-version": "2026-01-01"},
    )

    async def scenario():
        async with configured_model(
            config,
            key="synthetic-key" if mode == "custom-header" else None,
            transport=httpx2.MockTransport(serve),
        ) as model:
            await Agent(model).run("synthetic")

    asyncio.run(scenario())
    assert len(seen) == 1
    assert "authorization" not in seen[0].headers and "x-api-key" not in seen[0].headers
    assert seen[0].headers.get("x-gateway-key") == (
        "synthetic-key" if mode == "custom-header" else None
    )
    assert seen[0].headers["x-route"] == "blue"
    assert seen[0].url.params["api-version"] == "2026-01-01"
