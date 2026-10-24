"""有界JSON与不可变准备物；无网络、数据库或日志副作用。"""

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from types import UnionType
from typing import Any, TypeAliasType, overload

from pydantic import BaseModel, RootModel, TypeAdapter, ValidationError

from .errors import ProtocolError
from .models import Failure, Meta, Success


@dataclass(frozen=True, slots=True)
class Budget:
    max_bytes: int = 2 * 1024 * 1024
    max_depth: int = 32
    max_nodes: int = 100000

    def __post_init__(self):
        if any(
            type(v) is not int or v < 1 for v in (self.max_bytes, self.max_depth, self.max_nodes)
        ):
            raise ValueError("预算必须为正整数")


@lru_cache(maxsize=256)
def _cached_adapter(model) -> TypeAdapter:
    return TypeAdapter(model)


DEFAULT_BUDGET = Budget()


def adapter(model) -> TypeAdapter:
    return _cached_adapter(model)


def success_model(model):
    return Success.__class_getitem__(model)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复字段")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("非有限数值")


def load_json(raw: bytes, budget: Budget = DEFAULT_BUDGET) -> Any:
    if len(raw) > budget.max_bytes:
        raise ProtocolError("JSON超过结果预算")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        stack, nodes = [(value, 0)], 0
        while stack:
            current, depth = stack.pop()
            nodes += 1
            if nodes > budget.max_nodes or depth > budget.max_depth:
                raise ValueError("结构超限")
            if isinstance(current, float) and not math.isfinite(current):
                raise ValueError("数值溢出")
            if isinstance(current, dict):
                stack.extend((v, depth + 1) for v in current.values())
            elif isinstance(current, list):
                stack.extend((v, depth + 1) for v in current)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("JSON格式、数值或结构预算无效") from None


def _independent(value):
    # 使用字段值而非dump，先验证约束，防止被修改模型／construct绕过。
    if isinstance(value, RootModel):
        return _independent(value.root)
    if isinstance(value, BaseModel):
        return {name: _independent(getattr(value, name)) for name in type(value).model_fields} | {
            k: _independent(v) for k, v in (value.model_extra or {}).items()
        }
    if isinstance(value, dict):
        return {k: _independent(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_independent(v) for v in value)
    return value


def _encode(value, model, budget: Budget = DEFAULT_BUDGET):
    try:
        typed = adapter(model).validate_python(_independent(value), strict=True, by_name=True)
        plain = adapter(model).dump_python(typed, mode="json", warnings="error", by_alias=True)
        if isinstance(typed, (Success, Failure)):
            plain["meta"] = {
                k: v
                for k, v in plain["meta"].items()
                if k in {"trace_id", "contract_version"} or v not in (None, False, [], ())
            }
            if not plain.get("details"):
                plain.pop("details", None)
        raw = json.dumps(plain, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        load_json(raw, budget)
        revalidated = adapter(model).validate_json(raw, strict=True)
        return raw, revalidated
    # AttributeError 保留：validate_python/dump_python 在部分异常路径会以属性缺失结束，
    # 收窄会把这类畸形输入漏成未包装异常，违反边界只出 ProtocolError 的约定。
    except (ValueError, TypeError, RecursionError, AttributeError):
        raise ProtocolError("输出不符合已声明类型或JSON预算") from None


def encode(value, model, budget: Budget = DEFAULT_BUDGET) -> bytes:
    return _encode(value, model, budget)[0]


def decode[T](raw: bytes, model: type[T], budget: Budget = DEFAULT_BUDGET) -> T:
    value = load_json(raw, budget)
    if isinstance(model, type) and issubclass(model, (Success, Failure)):
        if not isinstance(value, dict) or not set(model.wire_fields) <= value.keys():
            raise ProtocolError("缺少必需协议字段")
        meta = value["meta"]
        if not isinstance(meta, dict) or not set(Meta.wire_fields) <= meta.keys():
            raise ProtocolError("缺少必需追踪或版本字段")
    try:
        return adapter(model).validate_json(raw, strict=True)
    except ValidationError:
        raise ProtocolError() from None


@dataclass(frozen=True, slots=True, init=False)
class PreparedSuccess[T]:
    body: bytes
    model: object
    operation: str
    replayed: bool

    def __init__(self):
        raise TypeError("请使用prepare_success校验工厂")


@overload
def prepare_success[T](
    value: object,
    model: type[T],
    meta: Meta,
    *,
    operation: str = "",
    budget: Budget = DEFAULT_BUDGET,
    registry=None,
) -> PreparedSuccess[T]: ...


@overload
def prepare_success(
    value: object,
    model: UnionType | TypeAliasType,
    meta: Meta,
    *,
    operation: str = "",
    budget: Budget = DEFAULT_BUDGET,
    registry=None,
) -> PreparedSuccess[object]: ...


def prepare_success(
    value, model, meta: Meta, *, operation: str = "", budget: Budget = DEFAULT_BUDGET, registry=None
) -> PreparedSuccess:
    # envelope先转为独立值，嵌套T不会因实例被信任而漏检。
    body, checked = _encode(
        {"code": 0, "message": "操作成功", "data": value, "meta": meta},
        success_model(model),
        budget,
    )
    from .registry import check_codes

    check_codes(checked, registry)
    result = object.__new__(PreparedSuccess)
    for name, val in (
        ("body", body),
        ("model", model),
        ("operation", operation),
        ("replayed", meta.replayed),
    ):
        object.__setattr__(result, name, val)
    return result
