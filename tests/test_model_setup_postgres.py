"""隔离PG、真实应用与合成供应商，验证未保存发现／测试／去重／启用。"""

import asyncio
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from app.infrastructure.models import ModelConfigurationRevision, ModelConnectionTest
from app.main import create_app
from cryptography.fernet import Fernet
from sqlalchemy import select
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytestmark = pytest.mark.live


def test_unsaved_configuration_flow_and_duplicate_probe(request, tmp_path, monkeypatch):
    settings = request.getfixturevalue("isolated_query_db")
    key = tmp_path / "model-root"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    enabled = settings.model_copy(update={"agent_secret_key_file": key})
    calls = []

    async def wire(request):
        calls.append(request)
        if request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "manual/fixture",
                            "object": "model",
                            "created": 1,
                            "owned_by": "fixture",
                        }
                    ],
                },
            )
        return httpx2.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "manual/fixture",
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

    monkeypatch.setattr(
        "app.application.model_runtime.model_transport",
        lambda *args, **kwargs: httpx2.MockTransport(wire),
    )

    async def scenario():
        factory, _, _, admin, user, *_ = await dataset(settings)
        web = create_app(enabled)
        app = web.state.application
        app.verifier.verify_async = AsyncMock(return_value=admin)
        path = "/api/v2/agent/configuration"
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=web),
                base_url="https://fixture.invalid",
                headers={"Authorization": "Bearer synthetic"},
            ) as client:
                before = (await client.get(path)).json()["data"]
                connection = {"model_url": "https://model.invalid/v1", "auth_mode": "none"}
                models = await client.post(
                    path + "/models", json={"version": before["version"], "connection": connection}
                )
                assert (
                    models.status_code == 200
                    and models.json()["data"]["items"][0]["id"] == "manual/fixture"
                ), models.text
                payload = {
                    "version": before["version"],
                    "draft": {**connection, "model": "manual/fixture"},
                    "request_key": "synthetic-once",
                    "acknowledge_cost": True,
                }
                tested = await client.post(path + "/probe", json=payload)
                assert tested.status_code == 200 and tested.json()["data"]["passed"], tested.text
                assert tested.json()["data"]["revision"] is None
                duplicate = await client.post(path + "/probe", json=payload)
                assert duplicate.status_code == 409
                after = (await client.get(path)).json()["data"]
                assert (
                    after["version"] == before["version"]
                    and after["draft_revision"] == before["draft_revision"]
                )
                assert after["last_test"]["revision"] is None
                assert [r.method for r in calls] == ["GET", "POST"]
                async with app.sessions() as db:
                    assert await db.scalar(select(ModelConfigurationRevision.revision)) is None
                    row = await db.scalar(
                        select(ModelConnectionTest).where(
                            ModelConnectionTest.request_key == "synthetic-once"
                        )
                    )
                    assert row.revision is None and row.state == "completed"
                app.verifier.verify_async.return_value = user
                assert (
                    await client.post(
                        path + "/models", json={"version": 0, "connection": connection}
                    )
                ).status_code == 403
                app.verifier.verify_async.return_value = admin
                saved = await client.post(
                    path + "/draft",
                    json={
                        "version": before["version"],
                        "draft": {**payload["draft"], "approved_model_data": True},
                    },
                )
                assert saved.status_code == 200, saved.text
                state = saved.json()["data"]
                applied = await client.post(
                    path + "/apply",
                    json={"version": state["version"], "revision": state["draft_revision"]},
                )
                assert applied.status_code == 200 and applied.json()["data"]["ready"], applied.text
                # 旧密钥不能因修改URL而静默外发。
                saved = await client.post(
                    path + "/draft",
                    json={
                        "version": applied.json()["data"]["version"],
                        "draft": {**connection, "auth_mode": "api-key", "model": "fixture"},
                        "secret_action": "replace",
                        "secret": "synthetic-model-key",
                    },
                )
                assert saved.status_code == 200, saved.text
                state = saved.json()["data"]
                changed = {
                    "version": state["version"],
                    "connection": {
                        **connection,
                        "model_url": "https://other.invalid/v1",
                        "auth_mode": "api-key",
                    },
                }
                refused = await client.post(path + "/models", json=changed)
                assert refused.status_code == 422
                allowed = await client.post(
                    path + "/models", json={**changed, "reuse_secret_at_new_url": True}
                )
                assert allowed.status_code == 200, allowed.text
                assert calls[-1].headers["authorization"] == "Bearer synthetic-model-key"
        finally:
            if app._agent:
                await app._agent.close()
            await app.verifier.aclose()
            await app.sessions.kw["bind"].dispose()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
