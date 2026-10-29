"""真实隔离PG18＋原应用＋共享Agent循环；身份和模型为显式测试替身。"""

import asyncio

import pytest
from sqlalchemy import select
from test_query_postgres import dataset, isolated_query_db  # noqa: F401 -- 复用受控临时PG夹具。

pytest.importorskip("office_agent", reason="需可选Agent依赖")

from app.application.agent_tools import ApplicationToolBridge
from app.infrastructure.models import Audit
from office_agent.config import Limits
from office_agent.gateway import Gateway
from office_agent.policy import AgentError
from office_agent.runtime import run_with_gateway
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.live


def test_real_pg_candidate_write_is_confirmed_by_human_gate_and_audited(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, app, call, _admin, user, project, _records, _ = await dataset(settings)

        async def authenticate():
            return user

        gateway = Gateway(ApplicationToolBridge(app, authenticate), Limits())
        approvals = []

        def model(messages, info):
            tools = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            if not tools:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "prepare_submit_record",
                            {
                                "project_id": project["id"],
                                "schema_revision": 1,
                                "values": {"v": "0.30", "tag": "agent-test"},
                            },
                            tool_call_id="prepare",
                        )
                    ]
                )
            if tools[-1].tool_name == "prepare_submit_record":
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "execute_write",
                            {"ticket": tools[-1].content["ticket"]},
                            tool_call_id="apply",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("已上报候选，尚未业务确认。")])

        async def approve(proposal):
            approvals.append(proposal.arguments)
            assert proposal.arguments["values"]["v"] == "0.30"
            assert proposal.preview["before"]["revision"] == 1
            return True

        try:
            result = await run_with_gateway(
                FunctionModel(model), gateway, "上报测试候选", approve=approve
            )
            assert result.status == "completed", result.explanation
            assert len(approvals) == 1
            received = result.receipts[-1]["result"]
            saved = await call(user, "record.get", {"identifier": received["id"]})
            assert saved["values"]["v"] == "0.30"
            async with factory() as db:
                rows = (
                    await db.scalars(
                        select(Audit).where(
                            Audit.resource_id == received["id"], Audit.action == "record.created"
                        )
                    )
                ).all()
                assert len(rows) == 1
            assert not result.outcome_unknown
            # 同一执行意图不能被框架恢复或重复批准重放。
            with pytest.raises(AgentError, match="已使用"):
                await gateway.execute(next(iter(gateway.proposals)))
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())


def test_real_pg_denies_agent_aggregate_all_scope(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, app, _call, _admin, user, project, _records, _ = await dataset(settings)

        async def authenticate():
            return user

        gateway = Gateway(ApplicationToolBridge(app, authenticate), Limits())
        try:
            await gateway.start()
            result = await gateway.read(
                "aggregate",
                {
                    "identifier": project["id"],
                    "revision": 1,
                    "query": {"measures": [{"alias": "sum", "op": "sum", "field": "v"}]},
                },
            )
            assert result["rows"] == [{"sum": "0.30"}]
            with pytest.raises(AgentError, match="403"):
                await gateway.read(
                    "aggregate",
                    {
                        "identifier": project["id"],
                        "revision": 1,
                        "scope": "all",
                        "query": {"measures": [{"alias": "n", "op": "count"}]},
                    },
                )
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
