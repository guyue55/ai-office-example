"""流式分页按实际读取预算停止，且所有结束路径关闭数据库游标。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.common.errors import DomainError
from app.infrastructure.pagination import stream_page
from sqlalchemy import literal, select


class Cursor:
    def __init__(self, count, *, cancel=False):
        self.count, self.consumed, self.closed, self.cancel = count, 0, False, cancel

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.cancel:
            raise asyncio.CancelledError()
        if self.consumed == self.count:
            raise StopAsyncIteration
        self.consumed += 1
        return {"id": self.consumed, "text": "x" * 100}

    async def close(self):
        self.closed = True


def test_stream_stops_at_byte_budget_and_continues_without_skipping(monkeypatch):
    monkeypatch.setattr("app.common.pagination.PAGE_BYTES", 250)

    async def exercise():
        stream = Cursor(101)
        db = SimpleNamespace(stream_scalars=AsyncMock(return_value=stream))
        result = await stream_page(
            db, select(literal(1)), lambda row: row, lambda row: row["id"], 100
        )
        assert [row["id"] for row in result["items"]] == [1, 2]
        assert result["next"] == "2"
        assert stream.consumed == 3 and stream.closed
        query = db.stream_scalars.call_args.args[0]
        assert query.get_execution_options()["yield_per"] == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("count,limit,next_value", [(2, 2, None), (3, 2, "2"), (0, 2, None)])
def test_stream_row_budget_and_end(count, limit, next_value):
    async def exercise():
        stream = Cursor(count)
        db = SimpleNamespace(stream_scalars=AsyncMock(return_value=stream))
        result = await stream_page(
            db, select(literal(1)), lambda row: row, lambda row: row["id"], limit
        )
        assert result["next"] == next_value and stream.closed
        assert stream.consumed <= limit + 1

    asyncio.run(exercise())


def test_stream_closes_on_oversized_row_and_cancellation(monkeypatch):
    monkeypatch.setattr("app.common.pagination.PAGE_BYTES", 10)

    async def exercise():
        for cancel, error in [(False, DomainError), (True, asyncio.CancelledError)]:
            stream = Cursor(2, cancel=cancel)
            db = SimpleNamespace(stream_scalars=AsyncMock(return_value=stream))
            with pytest.raises(error):
                await stream_page(
                    db, select(literal(1)), lambda row: row, lambda row: row["id"], 100
                )
            assert stream.closed

    asyncio.run(exercise())
