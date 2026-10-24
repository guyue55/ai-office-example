"""应用显式持有注册表；冻结后不共享可变定义。"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel

from .errors import ConfigurationError
from .models import ErrorInfo, Failure, Meta, Warning


@dataclass(frozen=True, slots=True)
class CodeSpec:
    code: int
    owner: str
    key: str
    message: str
    statuses: frozenset[int]
    severity: Literal["error", "warning"] = "error"
    definition: str = "受控操作结果"
    # availability 标记错误码生命状态：planned 只登记不出站，防止未实现能力被提前承诺。
    availability: Literal["current-source", "planned", "deprecated"] = "current-source"

    def __post_init__(self):
        if type(self.code) is not int or not 0 < self.code <= 2147483647:
            raise ConfigurationError("错误码必须为正整数")
        if not all(
            isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_.]{0,80}", v)
            for v in (self.owner, self.key)
        ):
            raise ConfigurationError("定义归属或键无效")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 500:
            raise ConfigurationError("缺少安全默认提示")
        if type(self.statuses) is not frozenset or any(
            type(s) is not int or not 400 <= s <= 599 for s in self.statuses
        ):
            raise ConfigurationError("HTTP错误状态定义无效")
        if (
            (self.severity == "error" and not self.statuses)
            or (self.severity == "warning" and self.statuses)
            or self.severity not in {"error", "warning"}
        ):
            raise ConfigurationError("错误／警告状态不一致")


def specs_from_rows(rows) -> tuple[CodeSpec, ...]:
    result = []
    for row in rows:
        allowed = row["allowed_http_statuses"]
        statuses = (
            range(allowed["min"], allowed["max"] + 1) if isinstance(allowed, dict) else allowed
        )
        result.append(
            CodeSpec(
                code=row["code"],
                owner=row["owner"],
                key=row["key"],
                message=row["message"],
                statuses=frozenset(statuses),
                severity=row["severity"],
                definition=row["definition"],
                availability=row.get("availability", "current-source"),
            )
        )
    return tuple(result)


class CodeRegistry:
    def __init__(self, application_id: str, specs=()):
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,80}", application_id):
            raise ConfigurationError("应用标识无效")
        self._application_id = application_id
        self._codes: Mapping[int, CodeSpec] = {}
        self._keys: Mapping[tuple[str, str], CodeSpec] = {}
        self._frozen = False
        self.register_many(specs)

    @property
    def application_id(self) -> str:
        return self._application_id

    def register_many(self, specs):
        if self._frozen:
            raise ConfigurationError("注册表已冻结")
        incoming = tuple(specs)
        codes, keys = set(self._codes), set(self._keys)
        for spec in incoming:
            key = (spec.owner, spec.key)
            if spec.code in codes or key in keys:
                raise ConfigurationError("错误定义重复")
            codes.add(spec.code)
            keys.add(key)
        for spec in incoming:
            self._codes = {**self._codes, spec.code: spec}
            self._keys = {**self._keys, (spec.owner, spec.key): spec}

    def freeze(self):
        self._codes = MappingProxyType(dict(self._codes))
        self._keys = MappingProxyType(dict(self._keys))
        self._frozen = True
        return self

    def by_code(self, code: int) -> CodeSpec | None:
        return self._codes.get(code) if type(code) is int else None

    def require(self, owner: str, key: str) -> CodeSpec:
        try:
            return self._keys[(owner, key)]
        except KeyError:
            raise ConfigurationError("错误定义未登记") from None

    def failure(self, code: int, status: int, meta: Meta, **kwargs) -> Failure:
        spec = self.by_code(code)
        if (
            not self._frozen
            or not spec
            or spec.severity != "error"
            or spec.availability != "current-source"
            or status not in spec.statuses
        ):
            raise ConfigurationError("出站错误未登记或状态不符")
        result = Failure(code=code, message=spec.message, meta=meta, **kwargs)
        check_codes(result, self)
        return result

    def export(self):
        result = {
            "application_id": self.application_id,
            "contract_version": "1",
            "entries": [
                {
                    "code": s.code,
                    "owner": s.owner,
                    "key": s.key,
                    "message": s.message,
                    "statuses": sorted(s.statuses),
                    "severity": s.severity,
                    "definition": s.definition,
                    "availability": s.availability,
                }
                for s in sorted(self._codes.values(), key=lambda s: s.code)
            ],
        }
        result["revision"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        return result

    @classmethod
    def from_export(cls, snapshot):
        content = {k: v for k, v in snapshot.items() if k != "revision"}
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        if digest != snapshot.get("revision") or content.get("contract_version") != "1":
            raise ConfigurationError("码表快照版本或指纹不符")
        specs = [
            CodeSpec(
                code=row["code"],
                owner=row["owner"],
                key=row["key"],
                message=row["message"],
                statuses=frozenset(row["statuses"]),
                severity=row["severity"],
                definition=row["definition"],
                # 旧快照无 availability 字段时默认 current-source，保持向后兼容。
                availability=row.get("availability", "current-source"),
            )
            for row in content["entries"]
        ]
        return cls(content["application_id"], specs).freeze()


def make_registry(application_id: str, specs=()) -> CodeRegistry:
    rows = json.loads(files("office_contracts").joinpath("core_codes.json").read_text())
    return CodeRegistry(application_id, (*specs_from_rows(rows), *specs)).freeze()


def check_codes(value, registry):
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, (Failure, ErrorInfo, Warning)):
            spec = registry.by_code(item.code) if registry is not None else None
            expected = "warning" if isinstance(item, Warning) else "error"
            if spec is None or spec.severity != expected:
                raise ConfigurationError("嵌套错误或警告未在当前应用登记")
            if spec.availability == "planned":
                raise ConfigurationError("planned 错误码不允许出站使用")
        if isinstance(item, BaseModel):
            stack.extend(getattr(item, name) for name in type(item).model_fields)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (tuple, list)):
            stack.extend(item)
