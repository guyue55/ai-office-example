"""有限查询语言：无原始SQL、任意表达式、JOIN或业务字段。"""

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.common.fields import FieldIdentifier
from app.common.inputs import IdentifierInput, Input, Revision

Scalar = str | int | bool


class Predicate(Input):
    # 条件值是数据；不能沿用管理表单的trim策略改变文本精确匹配语义。
    model_config = ConfigDict(str_strip_whitespace=False)
    field: FieldIdentifier
    op: Literal[
        "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_null", "not_null"
    ]
    values: list[Scalar] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def arity(self):
        expected = 0 if self.op in {"is_null", "not_null"} else 1
        if self.op in {"in", "not_in"}:
            if not self.values:
                raise ValueError("集合条件不能为空")
        elif len(self.values) != expected:
            raise ValueError("条件值数量不符合操作符")
        if any(isinstance(v, str) and (len(v) > 4000 or "\x00" in v) for v in self.values):
            raise ValueError("条件文本超过限制或含空字符")
        return self


class FilterGroup(Input):
    mode: Literal["and", "or"] = "and"
    predicates: list[Predicate] = Field(min_length=1, max_length=20)


class Group(Input):
    field: FieldIdentifier
    bucket: Literal["day", "month", "quarter", "year"] | None = None


class Measure(Input):
    alias: FieldIdentifier
    op: Literal["count", "sum", "avg", "min", "max"]
    field: FieldIdentifier | None = None

    @model_validator(mode="after")
    def field_required(self):
        if self.op != "count" and self.field is None:
            raise ValueError("该聚合操作需要字段")
        return self


class Order(Input):
    key: FieldIdentifier
    direction: Literal["asc", "desc"] = "asc"


class AggregateQuery(Input):
    # 外层组之间为AND；每组内可AND/OR。不开放无界递归表达式。
    filters: list[FilterGroup] = Field(default_factory=list, max_length=5)
    group_by: list[Group] = Field(default_factory=list, max_length=5)
    measures: list[Measure] = Field(min_length=1, max_length=8)
    order_by: list[Order] = Field(default_factory=list, max_length=5)
    max_groups: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def unique_outputs(self):
        names = [g.field for g in self.group_by] + [m.alias for m in self.measures]
        if len(set(names)) != len(names):
            raise ValueError("分组字段和聚合别名不能重复")
        keys = [o.key for o in self.order_by]
        if len(set(keys)) != len(keys) or set(keys) - set(names):
            raise ValueError("排序只能引用非重复的输出字段")
        if sum(len(g.predicates) for g in self.filters) > 40:
            raise ValueError("条件总数不能超过40")
        return self


class AggregateRequest(IdentifierInput):
    revision: Revision
    scope: Literal["own", "all"] = "own"
    query: AggregateQuery
