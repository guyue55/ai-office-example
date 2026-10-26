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


def test_conversation_http_mcp_and_native_history(request, tmp_path, monkeypatch):
    """真实迁移＋HTTP／MCP＋共享引擎，证明服务端续问而非仅页面消息拼接。"""
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, _application, _call, admin, user, *_ = await dataset(settings)
        config = tmp_path / "conversation-agent.toml"
        config.write_text(
            'service_url="https://fixture.invalid"\nmodel_url="https://model.invalid/v1"\nmodel="fixture"\napi_key_env="TEST_MODEL_KEY"\napproved_model_data=true\n'
        )
        enabled = settings.model_copy(update={"agent_config_file": config})
        prompts = []

        def response(messages, info):
            from pydantic_ai.messages import UserPromptPart

            prompts.extend(
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, UserPromptPart)
            )
            return ModelResponse(parts=[TextPart("服务器保存的合成回复")])

        @asynccontextmanager
        async def models(_config):
            yield FunctionModel(response)

        web = create_app(enabled)
        app = web.state.application
        app.verifier.verify_async = AsyncMock(return_value=user)
        app._agent = AgentCoordinator(app, model_context=models)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=web), base_url="https://fixture.invalid"
            ) as client:
                headers = {"Authorization": "Bearer isolated-test-token"}
                created = await client.post(
                    "/api/v2/agent/conversations",
                    headers=headers,
                    json={"title": "HTTP与MCP合成会话"},
                )
                assert created.status_code == 201
                conversation = created.json()["data"]
                listed = await client.get(
                    "/api/v2/agent/conversations?limit=10&archived=false", headers=headers
                )
                assert listed.status_code == 200
                assert listed.json()["data"]["items"][0]["id"] == conversation["id"]
                for number in range(2):
                    response_ = await client.post(
                        "/api/v2/agent/runs",
                        headers=headers,
                        json={
                            "prompt": f"合成第{number}轮",
                            "request_key": uuid4().hex,
                            "conversation_id": conversation["id"],
                            "context_page": "employees",
                        },
                    )
                    assert response_.status_code == 202
                    identifier = response_.json()["data"]["id"]
                    for _ in range(100):
                        detail = await client.get(
                            "/api/v2/agent/runs/" + identifier, headers=headers
                        )
                        if detail.json()["data"]["status"] == "completed":
                            break
                        await asyncio.sleep(0.02)
                    else:
                        raise AssertionError("合成会话未完成")
                assert (
                    len(prompts) == 2
                    and "服务器保存的合成回复" in prompts[-1]
                    and "合成第0轮" in prompts[-1]
                )
                server, _ = build_mcp(app)
                monkeypatch.setattr(
                    "app.entrypoints.mcp.server.get_access_token",
                    lambda: SimpleNamespace(token="isolated-test-token"),
                )
                result = await server.call_tool(
                    "get_assistant_conversation", {"identifier": conversation["id"]}
                )
                assert "合成第0轮" in json.dumps(result, ensure_ascii=False, default=str)
                app.verifier.verify_async.return_value = admin
                assert (
                    await client.get(
                        "/api/v2/agent/conversations/" + conversation["id"], headers=headers
                    )
                ).status_code == 404
                app.verifier.verify_async.return_value = user
                invalid = await client.post(
                    "/api/v2/agent/runs",
                    headers=headers,
                    json={
                        "prompt": "伪造历史",
                        "request_key": uuid4().hex,
                        "conversation_id": conversation["id"],
                        "message_history": [{"approved": True}],
                    },
                )
                assert invalid.status_code == 422
                snapshot = (
                    await client.get(
                        "/api/v2/agent/conversations/" + conversation["id"], headers=headers
                    )
                ).json()["data"]
                archived = await client.post(
                    "/api/v2/agent/conversations/archive",
                    headers=headers,
                    json={
                        "identifier": conversation["id"],
                        "version": snapshot["version"],
                        "archived": True,
                    },
                )
                assert archived.status_code == 200 and archived.json()["data"]["archived"]
        finally:
            await app._agent.close()
            await app.sessions.kw["bind"].dispose()
            await app.verifier.aclose()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
