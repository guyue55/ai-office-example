"""统一列表输入和组件负向验证；不以假数据替代真实PG统计验收。"""

import re
from unittest.mock import AsyncMock

import pytest
from app.application.commands import validate_command
from pydantic import ValidationError

pytest_plugins = ("test_web_console",)


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("access.list", {"kind": "roles", "page": True}),
        ("access.list", {"kind": "roles", "page": 0}),
        ("access.list", {"kind": "roles", "page": 1, "after": "cursor"}),
        ("access.list", {"kind": "roles", "actor": "不适用的条件"}),
        ("access.list", {"kind": "audit", "since": "2026-10-19"}),
        ("session.list", {"page": 1, "after": "cursor"}),
        ("account.history", {"page": 1, "after": "cursor"}),
    ],
)
def test_list_input_boundaries(operation, payload):
    with pytest.raises(ValidationError):
        validate_command(operation, payload)


@pytest.mark.parametrize(
    "query",
    ["page=0", "page=1&page=2", "unknown=1", "limit=true", "since=9999-12-31&until=9999-12-31"],
)
def test_web_invalid_filters_are_rejected(web, query):
    response = web.client.get("/console/audit?" + query)
    assert response.status_code == 422
    assert "trace" in response.text


def test_shared_numeric_pager_is_bounded_and_preserves_filters(web):
    original = web.application.call.side_effect

    async def call(principal, operation, payload, trace):
        if operation == "access.list":
            assert payload["page"] == 1 and payload["limit"] == 10
            return {
                "items": [],
                "has_more": True,
                "next": None,
                "pagination": {
                    "page": 1,
                    "page_size": 10,
                    "total": 2000000,
                    "pages": 200000,
                    "start": 1,
                    "end": 10,
                },
            }
        return await original(principal, operation, payload, trace)

    web.application.call = AsyncMock(side_effect=call)
    response = web.client.get("/console/roles?q=筛选内容")
    assert response.status_code == 200
    assert 'aria-current="page"' in response.text and "2000000" in response.text
    assert "page=2" in response.text and "q=" in response.text
    # 只约束分页自身，避免共用样式／脚本增长被误判为页码膨胀。
    pager = re.search(
        r'<nav class="pagination pagination-numbered".*?</nav>', response.text, re.DOTALL
    )
    assert pager is not None
    assert len(pager.group()) < 10000
    assert pager.group().count('aria-label="第 ') <= 4
    assert 'aria-label="跳转页码"' in response.text and 'aria-label="每页条数"' in response.text


def test_deep_numbered_query_has_explicit_budget():
    import asyncio
    from types import SimpleNamespace

    from app.common.errors import DomainError
    from app.infrastructure.models import Role
    from app.infrastructure.pagination import numbered_rows
    from sqlalchemy import select

    db = SimpleNamespace(scalar=AsyncMock(return_value=2000000), execute=AsyncMock())
    with pytest.raises(DomainError) as caught:
        asyncio.run(numbered_rows(db, select(Role).order_by(Role.id), 20000, 20))
    assert caught.value.status == 422
    db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "total,page,rows,start,end",
    [(1, 1, [], 0, 0), (11, 2, [], 0, 0), (0, 1, ["new"], 1, 1), (12, 2, ["a", "b"], 11, 12)],
)
def test_numbered_ranges_follow_actual_rows(total, page, rows, start, end):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app.infrastructure.models import Role
    from app.infrastructure.pagination import numbered_rows
    from sqlalchemy import select

    result = Mock()
    result.scalars.return_value.all.return_value = rows
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=total), execute=AsyncMock(return_value=result)
    )
    actual, pagination = asyncio.run(numbered_rows(db, select(Role), page, 10))
    assert actual == rows
    assert (pagination["start"], pagination["end"]) == (start, end)
    assert pagination["total"] == total
