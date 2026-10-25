"""真实PG任务执行、人工决定与重启安全中断；不调用付费模型。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_query_postgres import dataset, isolated_query_db  # noqa: F401 -- 注册隔离PG夹具。

pytest.importorskip("office_agent", reason="需可选Agent依赖")

from app.application.agent_runner import AgentRunner
from app.infrastructure.models import Audit, Employee
from app.modules.access.service import Access
from app.modules.agent.service import AgentRecords
from office_agent.config import Limits
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.live


@pytest.mark.parametrize("stop_after_commit", [False, True])
def test_hosted_runner_waits_for_owned_decision_then_records_result(request, stop_after_commit):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, app, _call, _admin, user, project, _records, _ = await dataset(settings)
        committed = asyncio.Event()
        committed_result = {}
        original_call = app.call

        async def delayed_call(principal, operation, payload, trace_id, **options):
            result = await original_call(principal, operation, payload, trace_id, **options)
            if operation == "record.create":
                committed_result.update(result)
                committed.set()
                if stop_after_commit:
                    await asyncio.Event().wait()  # 已提交而回执丢失的真实取消窗口。
            return result

        app.call = delayed_call

        def model(messages, info):
            tools = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            if not tools:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "prepare_write",
                            {
                                "operation": "submit_record",
                                "arguments": {
                                    "project_id": project["id"],
                                    "schema_revision": 1,
                                    "values": {"v": "0.40"},
                                },
                            },
                            tool_call_id="p",
                        )
                    ]
                )
            if tools[-1].tool_name == "prepare_write":
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "execute_write",
                            {"ticket": tools[-1].content["ticket"]},
                            tool_call_id="w",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("候选已上报，尚未业务确认。")])

        @asynccontextmanager
        async def models(_config):
            yield FunctionModel(model)

        async def authenticate():
            return user

        runner = AgentRunner(app, SimpleNamespace(limits=Limits(seconds=5)), model_context=models)
        try:
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()), exclusive=True)
                row, _ = await AgentRecords(access).create(
                    "测试", uuid4().hex, "a" * 64, per_user=2, total=8
                )
            runner.launch(row.id, authenticate)
            task = runner.tasks[row.id]
            for _ in range(100):
                pending = await runner.snapshot(row.id)
                if pending.status == "awaiting_confirmation":
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("未进入人工确认状态")
            assert pending.pending["arguments"]["values"]["v"] == "0.40"
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()))
                await AgentRecords(access).decide(
                    row.id, pending.version, pending.pending["ticket"], True
                )
            if stop_after_commit:
                await asyncio.wait_for(committed.wait(), 5)
                runner.cancel(row.id)
                await asyncio.gather(task, return_exceptions=True)
            else:
                await asyncio.wait_for(task, 5)
            saved = await runner.snapshot(row.id)
            assert saved.pending is None
            if stop_after_commit:
                assert saved.status == "outcome_unknown" and saved.write_inflight
            else:
                assert saved.status == "completed" and not saved.write_inflight
                assert saved.result["receipts"][-1]["status"] == "received"
            async with factory() as db:
                audit = (
                    await db.scalars(
                        select(Audit).where(
                            Audit.resource_id == committed_result["id"],
                            Audit.action == "record.created",
                        )
                    )
                ).all()
                assert len(audit) == 1 and audit[0].detail["agent_run_id"] == row.id
        finally:
            await runner.close()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())


def test_restart_marks_lost_runs_without_replay(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, app, _call, _admin, user, _project, _records, _ = await dataset(settings)
        runner = AgentRunner(app, SimpleNamespace(limits=Limits(seconds=5)))
        try:
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()), exclusive=True)
                store = AgentRecords(access)
                first, _ = await store.create(
                    "失去进程的任务", uuid4().hex, "a" * 64, per_user=3, total=8
                )
                second, _ = await store.create(
                    "已发出写入的任务", uuid4().hex, "b" * 64, per_user=3, total=8
                )
                second.status, second.write_inflight = "running", True
            await runner.recover()
            assert (await runner.snapshot(first.id)).status == "interrupted"
            assert (await runner.snapshot(second.id)).status == "outcome_unknown"
            assert not runner.tasks
        finally:
            await runner.close()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())


def test_disabled_employee_is_blocked_before_first_model_dispatch(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, app, _call, _admin, user, _project, _records, _ = await dataset(settings)
        identity = await app.call(user, "me", {}, str(uuid4()))
        entered, proceed = asyncio.Event(), asyncio.Event()
        dispatched = []

        @asynccontextmanager
        async def models(_config):
            entered.set()
            await proceed.wait()

            def model(messages, info):
                dispatched.append(True)
                return ModelResponse(parts=[TextPart("不应调用")])

            yield FunctionModel(model)

        async def authenticate():
            return user

        runner = AgentRunner(app, SimpleNamespace(limits=Limits(seconds=5)), model_context=models)
        try:
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()), exclusive=True)
                row, _ = await AgentRecords(access).create(
                    "停用测试", uuid4().hex, "a" * 64, per_user=2, total=8
                )
            runner.launch(row.id, authenticate)
            task = runner.tasks[row.id]
            await asyncio.wait_for(entered.wait(), 5)
            async with factory.begin() as db:
                employee = await db.get(Employee, identity["id"])
                employee.active = False
            proceed.set()
            await asyncio.wait_for(task, 5)
            assert not dispatched
            assert (await runner.snapshot(row.id)).status == "failed"
        finally:
            await runner.close()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
