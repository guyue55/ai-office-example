"""独立PG正式迁移＋HTTP管理＋真实共享Runner；模型与身份为明确合成夹具。"""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytest.importorskip("office_agent", reason="需可选Agent依赖")
from app.application.agent_service import AgentCoordinator
from app.infrastructure.models import (
    AgentRun,
    Audit,
    Employee,
    ModelConfigurationRevision,
    ModelConnectionTest,
)
from app.main import create_app
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.live


def test_managed_model_full_lifecycle(request, tmp_path):
    settings = request.getfixturevalue("isolated_query_db")
    key = tmp_path / "model-key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    enabled = settings.model_copy(
        update={
            "agent_model_url_policy": "allowlist",
            "agent_secret_key_file": key,
            "agent_model_urls": ["https://model.invalid/v1"],
        }
    )

    async def scenario():
        factory, _, _, admin, user, *_ = await dataset(settings)
        web = create_app(enabled)
        app = web.state.application
        app.verifier.verify_async = AsyncMock(return_value=admin)
        models_seen = []

        @asynccontextmanager
        async def models(config):
            models_seen.append(config.api)
            yield FunctionModel(
                lambda messages, info: ModelResponse(parts=[TextPart("合成连接成功")])
            )

        coordinator = AgentCoordinator(app, model_context=models)
        app._agent = coordinator
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=web),
                base_url="https://fixture.invalid",
                headers={"Authorization": "Bearer synthetic-token"},
            ) as client:
                path = "/api/v2/agent/configuration"
                assert (await client.get(path)).json()["data"]["source"] == "external"
                app.verifier.verify_async.return_value = user
                for suffix, body in [
                    ("", None),
                    (
                        "/draft",
                        {
                            "version": 0,
                            "draft": {"model_url": "https://model.invalid/v1", "model": "fixture"},
                        },
                    ),
                    ("/disable", {"version": 0}),
                ]:
                    response = (
                        await client.get(path)
                        if body is None
                        else await client.post(path + suffix, json=body)
                    )
                    assert response.status_code == 403
                app.verifier.verify_async.return_value = admin
                current = 0
                for api in ["openai-completions", "openai-responses", "anthropic-messages"]:
                    payload = {
                        "version": current,
                        "draft": {
                            "api": api,
                            "model_url": "https://model.invalid/v1",
                            "model": "fixture",
                            "approved_model_data": True,
                        },
                        "secret_action": "replace",
                        "secret": "synthetic-key-" + api,
                    }
                    saved = await client.post(path + "/draft", json=payload)
                    assert saved.status_code == 200, saved.text
                    state = saved.json()["data"]
                    assert (
                        "synthetic-key" not in saved.text and "secret_ciphertext" not in saved.text
                    )
                    assert state["secret_set"] is True
                    assert (await client.post(path + "/draft", json=payload)).status_code == 409
                    assert (
                        await client.post(
                            path + "/test",
                            json={"version": state["version"], "revision": state["draft_revision"]},
                        )
                    ).status_code == 422
                    tested = await client.post(
                        path + "/test",
                        json={
                            "version": state["version"],
                            "revision": state["draft_revision"],
                            "acknowledge_cost": True,
                        },
                    )
                    assert tested.status_code == 200 and tested.json()["data"]["passed"], (
                        tested.text
                    )
                    applied = await client.post(
                        path + "/apply",
                        json={"version": state["version"], "revision": state["draft_revision"]},
                    )
                    assert applied.status_code == 200 and applied.json()["data"]["ready"], (
                        applied.text
                    )
                    state = applied.json()["data"]
                    current = state["version"]
                    run = await client.post(
                        "/api/v2/agent/runs",
                        json={"prompt": "仅执行合成只读任务", "request_key": uuid4().hex},
                    )
                    assert run.status_code == 202, run.text
                    for _ in range(100):
                        task = (
                            await client.get("/api/v2/agent/runs/" + run.json()["data"]["id"])
                        ).json()["data"]
                        if task["status"] not in {"running", "queued"}:
                            break
                        await asyncio.sleep(0.02)
                    assert task["status"] == "completed", task
                assert set(models_seen) == {
                    "openai-completions",
                    "openai-responses",
                    "anthropic-messages",
                }
                # 两个标签同时保存，只有一个成功；keep保持密文而不是误清空。
                async with app.sessions() as db:
                    original_cipher = (
                        await db.get(ModelConfigurationRevision, state["draft_revision"])
                    ).secret_ciphertext
                keep = {"version": current, "draft": state["draft"], "secret_action": "keep"}
                pair = await asyncio.gather(
                    client.post(path + "/draft", json=keep), client.post(path + "/draft", json=keep)
                )
                assert sorted(r.status_code for r in pair) == [200, 409]
                state = next(r.json()["data"] for r in pair if r.status_code == 200)
                current = state["version"]
                async with app.sessions() as db:
                    assert (
                        await db.get(ModelConfigurationRevision, state["draft_revision"])
                    ).secret_ciphertext == original_cipher
                disallowed = await client.post(
                    path + "/draft",
                    json={
                        "version": current,
                        "draft": {**state["draft"], "model_url": "https://not-approved.invalid"},
                    },
                )
                assert disallowed.status_code == 422
                # 未获数据许可可保存及合成测试，但不能启用；原配置继续有效。
                pending = await client.post(
                    path + "/draft",
                    json={
                        "version": current,
                        "draft": {**state["draft"], "approved_model_data": False},
                    },
                )
                pending_state = pending.json()["data"]
                synthetic = await client.post(
                    path + "/test",
                    json={
                        "version": pending_state["version"],
                        "revision": pending_state["draft_revision"],
                        "acknowledge_cost": True,
                    },
                )
                assert synthetic.status_code == 200 and synthetic.json()["data"]["passed"]
                tested_status = (await client.get(path)).json()["data"]
                assert tested_status["last_test"]["revision"] == pending_state["draft_revision"]
                assert tested_status["last_test"]["result"]["category"] == "ok"

                @asynccontextmanager
                async def failed_model(config):
                    raise ValueError("provider-secret-do-not-expose")
                    yield  # pragma: no cover -- 保持异步上下文管理器协议。

                coordinator.model_context = failed_model
                failed_test = await client.post(
                    path + "/test",
                    json={
                        "version": pending_state["version"],
                        "revision": pending_state["draft_revision"],
                        "acknowledge_cost": True,
                    },
                )
                assert failed_test.status_code == 200 and not failed_test.json()["data"]["passed"]
                assert "provider-secret-do-not-expose" not in failed_test.text
                failed_status = (await client.get(path)).json()["data"]
                assert not failed_status["last_test"]["result"]["passed"]
                assert failed_status["active_revision"] == tested_status["active_revision"]
                coordinator.model_context = models
                blocked = await client.post(
                    path + "/apply",
                    json={
                        "version": pending_state["version"],
                        "revision": pending_state["draft_revision"],
                    },
                )
                assert blocked.status_code == 422
                state = (
                    await client.post(
                        path + "/draft",
                        json={"version": pending_state["version"], "draft": state["draft"]},
                    )
                ).json()["data"]
                current = state["version"]
                # 保存清除密钥的草稿不会影响仍启用的旧修订；失败应用保持旧Runner。
                cleared = await client.post(
                    path + "/draft",
                    json={"version": current, "draft": state["draft"], "secret_action": "clear"},
                )
                assert cleared.status_code == 200
                state = cleared.json()["data"]
                current = state["version"]
                old_runner = coordinator.runner
                failed = await client.post(
                    path + "/apply", json={"version": current, "revision": state["draft_revision"]}
                )
                assert failed.status_code == 422 and coordinator.runner is old_runner
                assert (await client.get(path)).json()["data"]["active_revision"] != state[
                    "draft_revision"
                ]
                # 重启从数据库恢复已启用修订，不能误选最新草稿。
                await coordinator.close()
                coordinator = AgentCoordinator(app, model_context=models)
                app._agent = coordinator
                await coordinator.start()
                assert (
                    coordinator.runner is not None
                    and coordinator.active_revision != state["draft_revision"]
                )
                # 运行／等待确认任务阻止切换，不靠前端计数。
                async with app.sessions.begin() as db:
                    previous = await db.scalar(select(AgentRun).limit(1))
                    previous.status = "awaiting_confirmation"
                    task_id = previous.id
                blocked = await client.post(path + "/disable", json={"version": current})
                assert blocked.status_code == 409
                async with app.sessions.begin() as db:
                    previous = await db.get(AgentRun, task_id)
                    previous.status = "completed"
                disabled = await client.post(path + "/disable", json={"version": current})
                assert disabled.status_code == 200 and not disabled.json()["data"]["ready"]
                await coordinator.close()
                coordinator = AgentCoordinator(app, model_context=models)
                app._agent = coordinator
                assert not (await client.get("/api/v2/agent/capabilities")).json()["data"]["ready"]
                assert coordinator.source == "disabled"
                rejected = await client.post(
                    "/api/v2/agent/runs", json={"prompt": "不能启动", "request_key": uuid4().hex}
                )
                assert rejected.status_code == 503
                async with app.sessions() as db:
                    revisions = list(await db.scalars(select(ModelConfigurationRevision)))
                    assert all("synthetic-key" not in json.dumps(row.config) for row in revisions)
                    assert all(
                        not row.secret_ciphertext or "synthetic-key" not in row.secret_ciphertext
                        for row in revisions
                    )
                    audits = list(
                        await db.scalars(select(Audit).where(Audit.action.like("agent.config.%")))
                    )
                    assert {
                        "agent.config.saved",
                        "agent.config.applied",
                        "agent.config.disabled",
                        "agent.config.tested",
                    } <= {a.action for a in audits}
                    assert "synthetic-key" not in json.dumps([a.detail for a in audits])
                    assert all(
                        r.model_revision is not None for r in await db.scalars(select(AgentRun))
                    )
                # 测试网络阶段并发保存与撤权：结果属于原修订，撤权不丢完成留痕。
                state = (await client.get(path)).json()["data"]
                saved = (
                    await client.post(
                        path + "/draft",
                        json={
                            "version": state["version"],
                            "draft": state["draft"],
                            "secret_action": "replace",
                            "secret": "synthetic-key-concurrent",
                        },
                    )
                ).json()["data"]
                entered, release = asyncio.Event(), asyncio.Event()

                @asynccontextmanager
                async def paused_model(config):
                    entered.set()
                    await release.wait()
                    yield FunctionModel(
                        lambda messages, info: ModelResponse(parts=[TextPart("OK")])
                    )

                coordinator.model_context = paused_model
                task = asyncio.create_task(
                    client.post(
                        path + "/test",
                        json={
                            "version": saved["version"],
                            "revision": saved["draft_revision"],
                            "acknowledge_cost": True,
                        },
                    )
                )
                await asyncio.wait_for(entered.wait(), 5)
                newer = await client.post(
                    path + "/draft",
                    json={
                        "version": saved["version"],
                        "draft": {**saved["draft"], "model": "newer-model"},
                    },
                )
                assert newer.status_code == 200
                async with app.sessions.begin() as db:
                    attempt = await db.scalar(
                        select(ModelConnectionTest).where(ModelConnectionTest.state == "started")
                    )
                    actor_id, attempt_id = attempt.actor_id, attempt.id
                    employee = await db.get(Employee, actor_id)
                    employee.active = False
                release.set()
                assert (await task).status_code == 403
                async with app.sessions.begin() as db:
                    attempt = await db.get(ModelConnectionTest, attempt_id)
                    assert attempt.state == "completed" and attempt.result["passed"]
                    assert attempt.revision == saved["draft_revision"]
                    employee = await db.get(Employee, actor_id)
                    employee.active = True
                status = (await client.get(path)).json()["data"]
                assert status["last_test"]["revision"] != status["draft_revision"]
                # 模拟进程在开始后中断，重启只标记未知／中断，不重发。
                async with app.sessions.begin() as db:
                    orphan = ModelConnectionTest(
                        revision=saved["draft_revision"],
                        actor_id=actor_id,
                        api="openai-completions",
                        model="interrupted",
                        state="started",
                    )
                    db.add(orphan)
                    await db.flush()
                    orphan_id = orphan.id
                await coordinator.close()
                coordinator = AgentCoordinator(app, model_context=models)
                app._agent = coordinator
                await coordinator.start()
                async with app.sessions() as db:
                    assert (await db.get(ModelConnectionTest, orphan_id)).state == "interrupted"
        finally:
            await coordinator.close()
            await app.verifier.aclose()
            await app.sessions.kw["bind"].dispose()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())


def test_no_key_gateway_saved_tested_applied_and_reloaded(request, tmp_path, monkeypatch):
    """真实隔离PG＋HTTP应用＋SDK协议；模型线缆为显式合成替身。"""
    import httpx2

    settings = request.getfixturevalue("isolated_query_db")
    key = tmp_path / "model-root"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    enabled = settings.model_copy(update={"agent_secret_key_file": key})
    seen = []

    async def serve(request):
        seen.append(request)
        assert "authorization" not in request.headers
        assert request.headers["x-route"] == "blue"
        assert request.url.params["api-version"] == "2026-01-01"
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

    monkeypatch.setattr(
        "app.application.model_runtime.model_transport",
        lambda *args, **kwargs: httpx2.MockTransport(serve),
    )

    async def scenario():
        factory, _, _, admin, *_ = await dataset(settings)
        web = create_app(enabled)
        app = web.state.application
        app.verifier.verify_async = AsyncMock(return_value=admin)
        coordinator = AgentCoordinator(app)
        app._agent = coordinator
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=web),
                base_url="https://fixture.invalid",
                headers={"Authorization": "Bearer synthetic"},
            ) as client:
                path = "/api/v2/agent/configuration"
                current = (await client.get(path)).json()["data"]
                saved = await client.post(
                    path + "/draft",
                    json={
                        "version": current["version"],
                        "secret_action": "clear",
                        "draft": {
                            "model_url": "https://model.invalid/v1",
                            "model": "fixture",
                            "auth_mode": "none",
                            "extra_headers": {"x-route": "blue"},
                            "extra_query": {"api-version": "2026-01-01"},
                            "approved_model_data": True,
                        },
                    },
                )
                assert saved.status_code == 200, saved.text
                state = saved.json()["data"]
                assert not state["secret_set"]
                command = {"version": state["version"], "revision": state["draft_revision"]}
                tested = await client.post(
                    path + "/test", json={**command, "acknowledge_cost": True}
                )
                assert tested.status_code == 200 and tested.json()["data"]["passed"], tested.text
                applied = await client.post(path + "/apply", json=command)
                assert applied.status_code == 200 and applied.json()["data"]["ready"], applied.text
                reloaded = (await client.get(path)).json()["data"]
                assert reloaded["active"]["auth_mode"] == "none"
                assert reloaded["active"]["extra_query"] == {"api-version": "2026-01-01"}
                assert len(seen) == 1
        finally:
            await coordinator.close()
            await app.sessions.kw["bind"].dispose()
            await app.verifier.aclose()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
