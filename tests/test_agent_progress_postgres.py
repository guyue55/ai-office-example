"""隔离PG验收进度持久化、审批版本稳定与权限变化隐藏。"""

import asyncio
from uuid import uuid4

import pytest
from app.application.agent_progress import ProgressWriter
from app.infrastructure.models import AgentRun, SetPermission
from app.modules.access.service import Access
from app.modules.agent.conversations import Conversations
from app.modules.agent.service import AgentRecords
from sqlalchemy import delete
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytestmark = pytest.mark.live


def test_progress_is_persisted_without_changing_approval_version_or_visibility(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, _app, _call, _admin, user, *_ = await dataset(settings)
        try:
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()), exclusive=True)
                conversation = await Conversations(access).create("进度测试")
                run, _ = await AgentRecords(access).create(
                    "权限查询",
                    "progress-request",
                    "a" * 64,
                    per_user=2,
                    total=8,
                    conversation_id=conversation["id"],
                )
                identifier, version = run.id, run.version
            writer = ProgressWriter(factory, identifier)
            await writer(
                {
                    "schema_version": 1,
                    "seq": 1,
                    "elapsed_ms": 25,
                    "stage": "reply",
                    "text": "合成进度",
                    "steps": [],
                    "truncated": False,
                }
            )
            async with factory() as db:
                row = await db.get(AgentRun, identifier)
                assert row.progress["text"] == "合成进度" and row.version == version
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()))
                detail = await Conversations(access).detail(conversation["id"])
                assert detail["turns"][0]["progress"]["seq"] == 1
            async with factory.begin() as db:
                await db.execute(
                    delete(SetPermission).where(SetPermission.permission_code == "record.create")
                )
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()))
                detail = await Conversations(access).detail(conversation["id"])
                assert detail["turns"][0]["history_hidden"]
                assert detail["turns"][0]["progress"] is None
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
