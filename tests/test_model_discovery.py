"""SDK目录与服务端出口适配；仅合成线缆，不访问供应商。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
from app.application.model_discovery import discover_models
from office_agent.config import AgentConfig
from office_agent.providers import configured_model


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
def test_three_protocol_model_directories_preserve_identifiers(api):
    seen = []

    async def serve(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer synthetic"
        return httpx2.Response(
            200,
            json={
                "data": [
                    {
                        "id": "gateway/custom-model",
                        "object": "model",
                        "created": 1,
                        "owned_by": "fixture",
                        "type": "model",
                        "display_name": "自定义模型",
                        "created_at": "2026-10-28T00:00:00Z",
                    }
                ],
                "object": "list",
                "has_more": False,
                "first_id": "gateway/custom-model",
                "last_id": "gateway/custom-model",
            },
        )

    config = AgentConfig(
        service_url="https://service.invalid",
        model_url="https://model.invalid"
        if api == "anthropic-messages"
        else "https://model.invalid/v1",
        model="__catalog__",
        api=api,
        approved_model_data=True,
    )

    @asynccontextmanager
    async def model(config):
        async with configured_model(
            config, key="synthetic", transport=httpx2.MockTransport(serve)
        ) as value:
            yield value

    result = asyncio.run(discover_models(config, SimpleNamespace(model_context=model)))
    assert result["passed"] and not result["truncated"]
    assert result["items"][0]["id"] == "gateway/custom-model"
    assert len(seen) == 1


def test_missing_directory_is_manual_entry_guidance_without_raw_error():
    async def serve(request):
        return httpx2.Response(404, json={"error": {"message": "secret-sentinel"}})

    config = AgentConfig(
        service_url="https://service.invalid",
        model_url="https://model.invalid/v1",
        model="__catalog__",
        auth_mode="none",
        approved_model_data=True,
    )

    @asynccontextmanager
    async def model(config):
        async with configured_model(config, transport=httpx2.MockTransport(serve)) as value:
            yield value

    result = asyncio.run(discover_models(config, SimpleNamespace(model_context=model)))
    assert not result["passed"] and "手动" in result["message"]
    assert "secret-sentinel" not in str(result)


def test_catalog_pages_are_bounded_and_duplicate_ids_are_not_rewritten():
    from unittest.mock import AsyncMock

    class Page:
        def __init__(self):
            self.data = [SimpleNamespace(id="alias/Exact-ID", display_name="fixture")]
            self.count = 0

        def has_next_page(self):
            return True

        async def get_next_page(self):
            self.count += 1
            return self

    page = Page()

    @asynccontextmanager
    async def model(config):
        yield SimpleNamespace(
            client=SimpleNamespace(models=SimpleNamespace(list=AsyncMock(return_value=page)))
        )

    result = asyncio.run(
        discover_models(
            SimpleNamespace(api="openai-completions", request_timeout=30),
            SimpleNamespace(model_context=model),
        )
    )
    assert result["passed"] and result["truncated"]
    assert result["items"] == [{"id": "alias/Exact-ID", "name": "fixture"}]
    assert page.count == 9


def test_catalog_item_limit_is_explicit():
    from unittest.mock import AsyncMock

    page = SimpleNamespace(
        data=[SimpleNamespace(id=f"model-{i}") for i in range(1001)], has_next_page=lambda: False
    )

    @asynccontextmanager
    async def model(config):
        yield SimpleNamespace(
            client=SimpleNamespace(models=SimpleNamespace(list=AsyncMock(return_value=page)))
        )

    result = asyncio.run(
        discover_models(
            SimpleNamespace(api="openai-completions", request_timeout=30),
            SimpleNamespace(model_context=model),
        )
    )
    assert len(result["items"]) == 1000 and result["truncated"]
