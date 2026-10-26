"""独立PG会话、权限修订和原任务兼容；模型仅用确定性测试替身。"""

import asyncio
from uuid import uuid4

import pytest
from app.application.agent_history import ConversationContext
from app.common.errors import DomainError
from app.infrastructure.models import AccessRevision, AgentRun, SetPermission
from app.modules.access.service import Access
from app.modules.agent.conversations import Conversations
from app.modules.agent.service import AgentRecords
from sqlalchemy import delete, update
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytestmark = pytest.mark.live


def test_conversation_ownership_serial_idempotency_archive_and_history(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, app, _call, admin, user, *_ = await dataset(settings)

        async def action(who, method, **kwargs):
            async with factory.begin() as db:
                access = await Access.create(db, who, str(uuid4()), exclusive=True)
                return await getattr(Conversations(access), method)(**kwargs)

        async def create_run(conversation_id, key):
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()), exclusive=True)
                return await AgentRecords(access).create(
                    "合成历史问题",
                    key,
                    "a" * 64,
                    per_user=2,
                    total=8,
                    conversation_id=conversation_id,
                    context_page="employees",
                )

        async def authenticate():
            return user

        try:
            conversation = await action(user, "create", title="合成会话")
            cid = conversation["id"]
            renamed = await action(user, "rename", identifier=cid, version=1, title="合成%_新会话")
            assert renamed["version"] == 2 and renamed["title"] == "合成%_新会话"
            with pytest.raises(DomainError) as stale:
                await action(user, "rename", identifier=cid, version=1, title="过期标题")
            assert stale.value.status == 409
            with pytest.raises(DomainError) as other:
                await action(admin, "rename", identifier=cid, version=2, title="越权标题")
            assert other.value.status == 404
            assert len((await action(user, "listing", q="%_"))["items"]) == 1
            assert not (await action(user, "listing", q="%_不存在"))["items"]
            with pytest.raises(DomainError) as denied:
                await action(admin, "detail", identifier=cid)
            assert denied.value.status == 404
            first, created = await create_run(cid, "first-key")
            same, replayed = await create_run(cid, "first-key")
            assert created and not replayed and first.id == same.id
            with pytest.raises(DomainError) as busy:
                await create_run(cid, "second-key")
            assert busy.value.status == 409
            with pytest.raises(DomainError):
                await action(user, "archive", identifier=cid, version=2, archived=True)
            async with factory.begin() as db:
                await db.execute(
                    update(AgentRun)
                    .where(AgentRun.id == first.id)
                    .values(status="completed", result={"explanation": "合成旧回复"})
                )
            second, _ = await create_run(cid, "second-key")
            context = ConversationContext(app, second, authenticate)
            prompt = await context.prompt(16000)
            assert "合成旧回复" in prompt and "不是系统指令" in prompt
            assert len((await context.prompt(128)).encode()) <= 128
            async with factory.begin() as db:
                await db.execute(
                    update(AgentRun).where(AgentRun.id == first.id).values(status="outcome_unknown")
                )
            uncertain = await context.prompt(16000)
            assert '"outcome_unknown": true' in uncertain and "不能自动重放" in uncertain
            view = await action(user, "detail", identifier=cid, limit=1)
            assert view["has_more"] and view["next"] == second.id
            previous = await action(user, "detail", identifier=cid, before=view["next"], limit=1)
            assert previous["turns"][0]["id"] == first.id
            async with factory.begin() as db:
                await db.execute(
                    update(AccessRevision).values(revision=AccessRevision.revision + 1)
                )
            # 单轮大回执不让会话无限增大，也不能静默伪装完整结果。
            async with factory.begin() as db:
                await db.execute(
                    update(AgentRun)
                    .where(AgentRun.id == first.id)
                    .values(result={"explanation": "x" * 150000})
                )
            large = await action(user, "detail", identifier=cid)
            large_turn = next(turn for turn in large["turns"] if turn["id"] == first.id)
            assert large_turn["detail_truncated"] and large_turn["result"] is None
            async with factory.begin() as db:
                await db.execute(
                    update(AgentRun)
                    .where(AgentRun.id == first.id)
                    .values(result={"items": list(range(5000))})
                )
            many_nodes = await action(user, "detail", identifier=cid)
            assert next(turn for turn in many_nodes["turns"] if turn["id"] == first.id)[
                "detail_truncated"
            ]
            unchanged = await action(user, "detail", identifier=cid)
            assert not unchanged["history_reset"]  # 无关全局版本变化不能误伤当前员工。
            await context.principal()
            async with factory.begin() as db:
                await db.execute(
                    delete(SetPermission).where(SetPermission.permission_code == "record.create")
                )
                await db.execute(
                    update(AccessRevision).values(revision=AccessRevision.revision + 1)
                )
            hidden = await action(user, "detail", identifier=cid)
            assert hidden["history_reset"]
            assert all(
                turn["result"] is None and turn["history_hidden"] for turn in hidden["turns"]
            )
            with pytest.raises(DomainError):
                await context.principal()
            async with factory.begin() as db:
                await db.execute(
                    update(AgentRun).where(AgentRun.id == second.id).values(status="completed")
                )
            detail = await action(user, "detail", identifier=cid)
            archived = await action(
                user, "archive", identifier=cid, version=detail["version"], archived=True
            )
            assert archived["archived"]
            with pytest.raises(DomainError):
                await create_run(cid, "archived-key")
            listed = await action(user, "listing", archived=True)
            assert listed["items"][0]["id"] == cid
            race_conversation = await action(user, "create", title="并发受理验收")
            raced = await asyncio.gather(
                create_run(race_conversation["id"], "race-key-a"),
                create_run(race_conversation["id"], "race-key-b"),
                return_exceptions=True,
            )
            assert sum(isinstance(item, tuple) for item in raced) == 1
            assert sum(isinstance(item, DomainError) and item.status == 409 for item in raced) == 1
            # 结束本轮成功任务，以便继续验证原独立入口。
            raced_row = next(item[0] for item in raced if isinstance(item, tuple))
            async with factory.begin() as db:
                await db.execute(
                    update(AgentRun).where(AgentRun.id == raced_row.id).values(status="completed")
                )
            # 无会话的既有任务无需历史配置，仍可独立运行。
            legacy, _ = await create_run(None, "legacy-key")
            assert (
                await ConversationContext(app, legacy, authenticate).prompt(16000) == legacy.prompt
            )
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
