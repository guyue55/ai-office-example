"""连接探针预算独立于普通数据库操作时限。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.application.model_runtime import test_runtime as probe
from office_agent.config import AgentConfig
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel


def test_slow_model_is_not_cut_short_by_database_budget():
    async def run():
        async def answer(messages, info):
            await asyncio.sleep(1.1)
            return ModelResponse(parts=[TextPart("OK")])

        @asynccontextmanager
        async def model(config):
            yield FunctionModel(answer)

        runner = SimpleNamespace(
            application=SimpleNamespace(settings=SimpleNamespace(operation_timeout=4)),
            model_context=model,
        )
        config = AgentConfig(
            service_url="https://app.invalid",
            model_url="https://model.invalid/v1",
            model="synthetic",
            approved_model_data=True,
        )
        assert (await probe(config, runner))["passed"]

    asyncio.run(run())
