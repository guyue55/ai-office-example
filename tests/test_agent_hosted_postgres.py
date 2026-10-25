"""新PG正式迁移后的HTTP+MCP任务闭环；模型和身份为显式隔离夹具。"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytest.importorskip("office_agent", reason="需可选Agent依赖")

from app.application.agent_service import AgentCoordinator
from app.entrypoints.mcp.server import build_mcp
from app.main import create_app
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.live


def test_hosted_http_mcp_owner_idempotence_and_reauthentication(request, tmp_path, monkeypatch):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, _app, _call, admin, user, _project, _records, _ = await dataset(settings)
        config = tmp_path / "agent.toml"
        config.write_text(
            'service_url = "https://fixture.invalid"\n'
            'model_url = "https://model.invalid/v1"\nmodel = "fixture"\n'
            'api_key_env = "ISOLATED_MODEL_KEY"\napproved_model_data = true\n'
        )
        enabled = settings.model_copy(update={"agent_config_file": config})

        @asynccontextmanager
        async def model_context(config):
            yield FunctionModel(
                lambda messages, info: ModelResponse(parts=[TextPart("已完成只读任务")])
            )

        web = create_app(enabled)
        app = web.state.application
        verifier = AsyncMock(return_value=user)
        app.verifier.verify_async = verifier
        app._agent = AgentCoordinator(app, model_context=model_context)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=web), base_url="http://testserver"
            ) as client:
                headers = {"Authorization": "Bearer isolated-test-token"}
                assert (await client.get("/api/v2/agent/runs")).status_code == 401
                cap = await client.get("/api/v2/agent/capabilities", headers=headers)
                assert cap.json()["data"]["ready"] is True
                body = {"prompt": "查看本人工作状态", "request_key": uuid4().hex}
                result = await client.post("/api/v2/agent/runs", headers=headers, json=body)
                assert result.status_code == 202 and result.json()["code"] == 0
                identifier = result.json()["data"]["id"]
                repeat = await client.post("/api/v2/agent/runs", headers=headers, json=body)
                assert repeat.json()["data"]["id"] == identifier
                assert repeat.json()["meta"]["replayed"] is True
                for _ in range(100):
                    detail = await client.get("/api/v2/agent/runs/" + identifier, headers=headers)
                    if detail.json()["data"]["status"] == "completed":
                        break
                    await asyncio.sleep(0.02)
                else:
                    raise AssertionError("任务未完成")
                assert detail.json()["data"]["result"]["explanation"] == "已完成只读任务"
                assert verifier.await_count >= 5  # HTTP、模型请求和身份初始化均真实复核。
                verifier.return_value = admin
                denied = await client.get("/api/v2/agent/runs/" + identifier, headers=headers)
                assert denied.status_code == 404
                verifier.return_value = user
                invalid = await client.post(
                    "/api/v2/agent/runs/decision",
                    headers=headers,
                    json={
                        "identifier": identifier,
                        "version": 1,
                        "proposal_id": "forged",
                        "approve": True,
                        "history": [],
                    },
                )
                assert invalid.status_code == 422
                server, _ = build_mcp(app)
                monkeypatch.setattr(
                    "app.entrypoints.mcp.server.get_access_token",
                    lambda: SimpleNamespace(token="isolated-test-token"),
                )
                response = await server.call_tool("get_assistant_run", {"identifier": identifier})
                assert identifier in json.dumps(response, default=str)
                invalid = await client.get("/api/v2/agent/runs?unknown=1", headers=headers)
                assert invalid.status_code == 422
        finally:
            await app._agent.close()
            await app.sessions.kw["bind"].dispose()
            await app.verifier.aclose()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
