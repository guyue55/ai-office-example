"""真实独立PG：模型结束后取消、重复取消、收尾失败及不重启恢复。"""

import asyncio
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytest.importorskip("office_agent")
from app.application.agent_service import AgentCoordinator
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.models import ModelConnectionTest

pytestmark = pytest.mark.live


@pytest.mark.parametrize("operation", ["agent.config.test", "agent.config.probe"])
def test_cancellation_and_finalize_failure_without_restarting(
    request, tmp_path, monkeypatch, operation
):
    settings = request.getfixturevalue("isolated_query_db")
    key = tmp_path / "synthetic-root"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    enabled = settings.model_copy(update={"agent_secret_key_file": key})

    async def run():
        factory, _, _, admin, *_ = await dataset(settings)
        app = Application(enabled, factory, None)
        coordinator = AgentCoordinator(app)
        app._agent = coordinator
        hold = None
        calls = 0

        async def call(operation, payload):
            return await app.call(admin, operation, payload, str(uuid4()))

        async def row_state(identifier):
            async with factory() as db:
                row = await db.get(ModelConnectionTest, identifier)
                return row.state, row.result

        try:
            state = await call(
                "agent.config.save",
                {
                    "version": (await call("agent.config.get", {}))["version"],
                    "draft": {"model": "fixture", "model_url": "https://model.invalid/v1"},
                    "secret_action": "replace",
                    "secret": "synthetic",
                },
            )
            command = {
                "version": state["version"],
                "revision": state["draft_revision"],
                "acknowledge_cost": True,
            }
            result = {
                "passed": True,
                "category": "ok",
                "message": "synthetic",
                "suggestion": "synthetic",
                "elapsed_ms": 1,
            }
            for mode in ("after_network_cancel", "network_double_cancel", "finalize_timeout"):
                current_command = (
                    command
                    if operation == "agent.config.test"
                    else {
                        "version": state["version"],
                        "draft": state["draft"],
                        "acknowledge_cost": True,
                        "request_key": uuid4().hex,
                    }
                )
                entered = asyncio.Event()
                identifiers = []

                async def network(
                    config, runner, *, mode=mode, entered=entered, identifiers=identifiers
                ):
                    nonlocal hold, calls
                    calls += 1
                    hold = factory()
                    await hold.begin()
                    row = await hold.scalar(
                        select(ModelConnectionTest)
                        .where(ModelConnectionTest.state == "started")
                        .with_for_update()
                    )
                    identifiers.append(row.id)
                    entered.set()
                    if mode == "network_double_cancel":
                        await asyncio.Event().wait()
                    return result

                monkeypatch.setattr("app.application.agent_service.test_runtime", network)
                coordinator.tests.timeout = 1 if mode == "finalize_timeout" else 5
                task = asyncio.create_task(call(operation, current_command))
                await asyncio.wait_for(entered.wait(), 5)
                if mode == "network_double_cancel":
                    task.cancel()
                # 等到独立收尾实际开始，避免依赖固定睡眠碰巧命中取消窗口。
                async with asyncio.timeout(2):
                    while not coordinator.tests.tasks:
                        await asyncio.sleep(0.001)
                if mode != "finalize_timeout":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert coordinator.tests.active
                    before = calls
                    with pytest.raises(DomainError) as error:
                        await call(operation, current_command)
                    assert error.value.status == 409 and calls == before
                    # 查询不会将仍在收尾的记录误恢复。
                    assert (await call("agent.config.get", {}))["last_test"]["state"] == "started"
                else:
                    with pytest.raises(DomainError) as error:
                        await task
                    assert error.value.status == 503 and not coordinator.tests.active
                await hold.rollback()
                await hold.close()
                hold = None
                await coordinator.tests.close()
                status = await call("agent.config.get", {})
                outcome, stored = await row_state(identifiers[0])
                assert outcome == ("completed" if mode == "after_network_cancel" else "interrupted")
                assert bool(stored) == (mode == "after_network_cancel")
                assert status["last_test"]["state"] == outcome
            assert calls == 3
            # 已知结果却找不到开始记录，不能向上层报告保存成功。
            missing = str(uuid4())
            coordinator.tests.active.add(missing)
            assert not await coordinator.tests.finish(
                missing, str(uuid4()), state["draft_revision"], str(uuid4()), result
            )
            assert missing not in coordinator.tests.active
        finally:
            if hold is not None:
                await hold.rollback()
                await hold.close()
            await coordinator.close()
            await factory.kw["bind"].dispose()

    asyncio.run(run())
