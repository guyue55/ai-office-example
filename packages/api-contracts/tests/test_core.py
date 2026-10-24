import hashlib
import json
from datetime import date

import pytest
from office_contracts import (
    CodeRegistry,
    CodeSpec,
    ConfigurationError,
    Meta,
    ProtocolError,
    Success,
    decode,
    make_registry,
    prepare_success,
)
from office_contracts.codec import Budget, load_json
from office_contracts.collections import BatchResult
from office_contracts.execution import TaskResult, check_transition
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Item(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    day: date
    quantity: int = Field(gt=0)


@pytest.mark.parametrize("code", [False, 0.0, "0", 1, -1])
def test_success_code_exact(code):
    with pytest.raises(ValidationError):
        Success[int](code=code, data=1, meta=Meta(trace_id="t"))


def test_prepared_is_independent_and_dates_roundtrip():
    obj = Item(day=date(2026, 10, 18), quantity=1)
    prepared = prepare_success(obj, Item, Meta(trace_id="t"), operation="test")
    obj.quantity = -1
    assert decode(prepared.body, Success[Item]).data.quantity == 1
    with pytest.raises(ProtocolError):
        prepare_success(obj, Item, Meta(trace_id="t"))
    with pytest.raises(ProtocolError):
        prepare_success(obj.model_copy(update={"quantity": -2}), Item, Meta(trace_id="t"))


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'"\xff"', b"{}{}"]
)
def test_json_rejects_ambiguous_or_nonfinite(raw):
    with pytest.raises(ProtocolError):
        load_json(raw)


def test_json_budget_and_null():
    with pytest.raises(ProtocolError):
        load_json(b'{"x":[[1]]}', Budget(max_depth=1))
    assert b'"data":null' in prepare_success(None, type(None), Meta(trace_id="t")).body


def test_registry_isolation_and_freeze():
    a = CodeSpec(900001, "inventory", "missing", "库存不存在", frozenset({404}))
    b = CodeSpec(900001, "orders", "missing", "订单不存在", frozenset({409}))
    one, two = make_registry("one", [a]), make_registry("two", [b])
    assert one.by_code(900001).message == "库存不存在"
    assert two.by_code(900001).message == "订单不存在"
    with pytest.raises(ConfigurationError):
        one.register_many([b])
    with pytest.raises(ConfigurationError):
        make_registry("bad", [a, b])
    with pytest.raises(ConfigurationError):
        one.failure(900001, 409, Meta(trace_id="t"))


def test_planned_codes_registered_but_not_exportable():
    planned = CodeSpec(
        900002, "quota", "exhausted", "配额不足", frozenset({429}), availability="planned"
    )
    registry = make_registry("plan", [planned])
    assert registry.by_code(900002).availability == "planned"
    with pytest.raises(ConfigurationError):
        registry.failure(900002, 429, Meta(trace_id="t"))
    snapshot = registry.export()
    assert next(r["availability"] for r in snapshot["entries"] if r["code"] == 900002) == "planned"
    assert CodeRegistry.from_export(snapshot).export() == snapshot


def test_from_export_defaults_availability_for_legacy_snapshots():
    snapshot = make_registry("legacy").export()
    for row in snapshot["entries"]:
        row.pop("availability", None)
    snapshot["revision"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in snapshot.items() if k != "revision"},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    registry = CodeRegistry.from_export(snapshot)
    assert all(row["availability"] == "current-source" for row in registry.export()["entries"])


def test_batch_correspondence_and_states():
    raw = '{"status":"partial","items":[{"id":"a","status":"succeeded","code":0,"message":"ok","data":1},{"id":"b","status":"failed","code":404001,"message":"不存在","data":null}]}'.encode()
    batch = decode(raw, BatchResult[int])
    batch.check_request_ids(("b", "a"))
    with pytest.raises(ValueError):
        batch.check_request_ids(("a",))
    with pytest.raises(ProtocolError):
        decode(raw.replace(b'"partial"', b'"succeeded"'), BatchResult[int])
    with pytest.raises(ValueError):
        check_transition("succeeded", "running")
    assert (
        decode(
            b'{"id":"t","status":"failed","error":{"code":500001,"message":"failed"}}',
            TaskResult[int],
        ).status
        == "failed"
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"message":"ok","data":1,"meta":{"trace_id":"t","contract_version":"1"}}',
        b'{"code":0,"message":"ok","data":1,"meta":{"trace_id":"t"}}',
    ],
)
def test_wire_fields_required_even_with_python_defaults(raw):
    with pytest.raises(ProtocolError):
        decode(raw, Success[int])
    assert {"code", "message", "data", "meta"} <= set(Success[int].model_json_schema()["required"])


def test_codebook_snapshot_fingerprint():
    from office_contracts import CodeRegistry

    snapshot = make_registry("inventory").export()
    assert CodeRegistry.from_export(snapshot).export() == snapshot
    snapshot["entries"][0]["message"] = "tampered"
    with pytest.raises(ConfigurationError):
        CodeRegistry.from_export(snapshot)


def test_root_model_keeps_actual_wire_shape():
    from pydantic import RootModel

    class Identifiers(RootModel[tuple[int, ...]]):
        pass

    prepared = prepare_success(Identifiers((1, 2)), Identifiers, Meta(trace_id="t"))
    assert decode(prepared.body, Success[Identifiers]).data.root == (1, 2)
