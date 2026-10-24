"""内容过大时及时终止流并释放游标，不先加载完整结果。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.common.errors import DomainError
from app.modules.data.service import Data


def test_export_stops_at_byte_budget_and_closes_stream():
    class Stream:
        def __init__(self):
            self.consumed = 0
            self.close = AsyncMock()

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.consumed += 1
            return SimpleNamespace(
                record_id=str(self.consumed),
                revision=1,
                schema_revision=1,
                values={f"f{i}": "x" * 4000 for i in range(100)},
                confirmed_by="tester",
                confirmed_at=datetime.now(UTC),
                reason=None,
            )

    async def run():
        stream = Stream()
        db = SimpleNamespace(stream_scalars=AsyncMock(return_value=stream))
        access = SimpleNamespace(db=db, need=Mock(), allows=Mock(return_value=True))
        data = Data(access)
        data.project = AsyncMock()
        with pytest.raises(DomainError) as caught:
            await data.export_confirmed("project", max_records=5000)
        assert (
            caught.value.status == 422 and caught.value.code == "confirmed_export_budget_exceeded"
        )
        assert stream.consumed == 4
        stream.close.assert_awaited_once()

    asyncio.run(run())


def test_export_stops_at_structure_budget_and_closes_stream():
    class Stream:
        def __init__(self):
            self.consumed = 0
            self.close = AsyncMock()

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.consumed += 1
            return SimpleNamespace(
                record_id=str(self.consumed),
                revision=1,
                schema_revision=1,
                values={f"f{i}": True for i in range(100)},
                confirmed_by="tester",
                confirmed_at=datetime.now(UTC),
                reason=None,
            )

    async def run():
        stream = Stream()
        db = SimpleNamespace(stream_scalars=AsyncMock(return_value=stream))
        access = SimpleNamespace(db=db, need=Mock(), allows=Mock(return_value=True))
        data = Data(access)
        data.project = AsyncMock()
        with pytest.raises(DomainError) as caught:
            await data.export_confirmed("project", max_records=5000)
        assert (
            caught.value.status == 422 and caught.value.code == "confirmed_export_budget_exceeded"
        )
        assert stream.consumed == 917  # 每项108节点；尚未超过条数或字节上限。
        stream.close.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("row_count,large_text", [(3, True), (916, False)])
def test_export_near_budget_survives_full_envelope_and_client_decode(row_count, large_text):
    import httpx
    from app.application.codebook import build_registry
    from app.modules.data.outputs import ConfirmedExport
    from office_contracts import Meta, Success, prepare_success
    from office_contracts.integrations.httpx import request_json

    class Stream:
        close = AsyncMock()

        def __init__(self):
            self.rows = iter(range(row_count))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                index = next(self.rows)
            except StopIteration:
                raise StopAsyncIteration from None
            return SimpleNamespace(
                record_id=str(index),
                revision=1,
                schema_revision=1,
                values={f"f{i}": "x" * 4000 if large_text else True for i in range(100)},
                confirmed_by="tester",
                confirmed_at=datetime.now(UTC),
                reason=None,
            )

    async def run():
        stream = Stream()
        db = SimpleNamespace(stream_scalars=AsyncMock(return_value=stream))
        data = Data(SimpleNamespace(db=db, need=Mock(), allows=Mock(return_value=True)))
        data.project = AsyncMock()
        result = await data.export_confirmed("project", max_records=5000)
        registry = build_registry()
        prepared = prepare_success(
            result, ConfirmedExport, Meta(trace_id="budget-test"), registry=registry
        )
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=prepared.body, headers={"Content-Type": "application/json"}
                )
            )
        ) as client:
            decoded = request_json(
                client, "GET", "http://test/export", ConfirmedExport, registry=registry
            )
        assert isinstance(decoded, Success) and len(decoded.data.items) == row_count
        stream.close.assert_awaited_once()

    asyncio.run(run())
