"""SDK配置边界；不连接真实供应商，不让宿主环境打开内容遥测。"""

import asyncio

import anthropic
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
