"""真实临时PG验证任务归属、请求幂等、容量、审批绑定与持久历史。"""

import asyncio
from uuid import uuid4

import pytest
from app.common.errors import DomainError
from app.modules.access.service import Access
from app.modules.agent.service import AgentRecords, run_view
from test_query_postgres import dataset, isolated_query_db  # noqa: F401 -- 注册受控临时PG夹具。

pytestmark = pytest.mark.live


def test_run_storage_owner_idempotency_capacity_and_decision(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory, _app, _call, admin, user, _project, _records, _ = await dataset(settings)

        async def operation(who, method, **kwargs):
            async with factory.begin() as db:
                access = await Access.create(db, who, str(uuid4()), exclusive=True)
                records = AgentRecords(access)
                return await getattr(records, method)(**kwargs)

        try:
            row, created = await operation(
                user,
                "create",
                prompt="测试任务",
                request_key="same-key",
                digest="a" * 64,
                per_user=2,
                total=8,
            )
            assert created and row.status == "queued"
            same, created = await operation(
                user,
                "create",
                prompt="测试任务",
                request_key="same-key",
                digest="a" * 64,
                per_user=2,
                total=8,
            )
            assert not created and same.id == row.id
            with pytest.raises(DomainError) as changed:
                await operation(
                    user,
                    "create",
                    prompt="其他任务",
                    request_key="same-key",
                    digest="b" * 64,
                    per_user=2,
                    total=8,
                )
            assert changed.value.status == 409
            with pytest.raises(DomainError) as cross_user:
                await operation(admin, "owned", identifier=row.id)
            assert cross_user.value.status == 404
            await operation(
                user,
                "create",
                prompt="第二任务",
                request_key="second-key",
                digest="b" * 64,
                per_user=2,
                total=8,
            )
            with pytest.raises(DomainError) as full:
                await operation(
                    user,
                    "create",
                    prompt="第三任务",
                    request_key="third-key",
                    digest="c" * 64,
                    per_user=2,
                    total=8,
                )
            assert full.value.status == 429
            async with factory.begin() as db:
                access = await Access.create(db, user, str(uuid4()))
                stored = await AgentRecords(access).owned(row.id, lock=True)
                stored.status = "awaiting_confirmation"
                stored.pending = {"ticket": "server-issued", "operation": "data", "after": {"a": 1}}
            with pytest.raises(DomainError):
                await operation(
                    user, "decide", identifier=row.id, version=1, proposal_id="forged", approve=True
                )
            decided = await operation(
                user,
                "decide",
                identifier=row.id,
                version=1,
                proposal_id="server-issued",
                approve=False,
            )
            assert decided["pending"]["decision"] is False
            assert decided["version"] == 2
            with pytest.raises(DomainError):
                await operation(
                    user,
                    "decide",
                    identifier=row.id,
                    version=2,
                    proposal_id="server-issued",
                    approve=True,
                )
            listing = await operation(user, "listing", page=1, limit=1)
            assert len(listing["items"]) == 1
            assert listing["pagination"]["total"] == 2 and listing["has_more"]
            assert "pending" not in listing["items"][0]
            saved = await operation(user, "owned", identifier=row.id)
            assert run_view(saved)["pending"]["decision"] is False
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
