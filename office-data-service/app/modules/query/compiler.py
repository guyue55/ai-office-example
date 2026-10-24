"""只编译已授权数据源的标量列；不加载原始记录到Python做聚合。"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Numeric, and_, cast, func, or_, select, true

from app.common.errors import DomainError, require
from app.common.fields import validate_values
from app.modules.query.schemas import AggregateQuery


def column(source, field):
    value = source.c["values"][field["id"]].astext
    kind = field["type"]
    if kind in {"decimal", "integer"}:
        return cast(value, Numeric)
    if kind == "date":
        return cast(value, Date)
    if kind == "boolean":
        return cast(value, Boolean)
    return value


def compile_query(source, fields: list[dict], revision: int, query: AggregateQuery):
    """source须由适配器先施加授权/状态限制，含values与schema_revision两列。"""
    known = {f["id"]: f for f in fields}

    def definition(key):
        require(key in known, 422, "unknown_field", "查询引用未发布字段")
        return known[key]

    selected = select(source).where(source.c.schema_revision == revision).cte("query_schema")
    clauses = []
    for group_index, filter_group in enumerate(query.filters):
        predicates = []
        for predicate_index, predicate in enumerate(filter_group.predicates):
            field = definition(predicate.field)
            expr = column(selected, field)
            op = predicate.op
            if op in {"is_null", "not_null"}:
                predicates.append(expr.is_(None) if op == "is_null" else expr.is_not(None))
                continue
            ordered = op in {"gt", "gte", "lt", "lte"}
            require(
                not ordered or field["type"] in {"integer", "decimal", "date"},
                422,
                "invalid_query_operation",
                "该字段类型不支持范围比较",
            )
            require(
                op != "contains" or field["type"] == "text",
                422,
                "invalid_query_operation",
                "包含操作仅支持文本字段",
            )
            values = []
            value: Any
            for value_index, value in enumerate(predicate.values):
                try:
                    checked = validate_values([{**field, "required": True}], {field["id"]: value})
                except DomainError as error:
                    # 复用值校验，诊断路径仍指向查询合同，不能误指记录写入的values对象。
                    error.details = [
                        {
                            "path": [
                                "query",
                                "filters",
                                str(group_index),
                                "predicates",
                                str(predicate_index),
                                "values",
                                str(value_index),
                            ],
                            "code": error.code,
                        }
                    ]
                    raise
                value = checked[field["id"]]
                if field["type"] in {"decimal", "integer"}:
                    value = Decimal(value)
                elif field["type"] == "date":
                    value = date.fromisoformat(value)
                values.append(value)
            if op == "in":
                clause = expr.in_(values)
            elif op == "not_in":
                clause = expr.not_in(values)
            elif op == "contains":
                clause = expr.contains(values[0], autoescape=True)
            else:
                clause = {
                    "eq": expr.__eq__,
                    "ne": expr.__ne__,
                    "gt": expr.__gt__,
                    "gte": expr.__ge__,
                    "lt": expr.__lt__,
                    "lte": expr.__le__,
                }[op](values[0])
            predicates.append(clause)
        clauses.append((and_ if filter_group.mode == "and" else or_)(*predicates))
    filtered = select(selected).where(*clauses).cte("query_filtered")
    groups, measures, columns = [], [], []
    for group in query.group_by:
        field = definition(group.field)
        expr = column(filtered, field)
        if group.bucket is not None:
            require(
                field["type"] == "date", 422, "invalid_query_operation", "日期分桶仅支持日期字段"
            )
            expr = cast(func.date_trunc(group.bucket, cast(expr, DateTime(timezone=False))), Date)
        groups.append(expr.label(group.field))
        columns.append(
            {"key": group.field, "type": field["type"], "operation": group.bucket or "group"}
        )
    for measure in query.measures:
        field = definition(measure.field) if measure.field is not None else None
        kind = field["type"] if field else "integer"
        require(
            measure.op not in {"sum", "avg"} or kind in {"decimal", "integer"},
            422,
            "invalid_query_operation",
            "求和与平均仅支持数值字段",
        )
        require(
            measure.op not in {"min", "max"} or kind != "boolean",
            422,
            "invalid_query_operation",
            "布尔字段不支持极值聚合",
        )
        argument = column(filtered, field) if field else None
        expr = func.count() if argument is None else getattr(func, measure.op)(argument)
        if measure.op == "avg":
            expr = func.round(expr, 8)
        measures.append(expr.label(measure.alias))
        columns.append(
            {
                "key": measure.alias,
                "type": "integer"
                if measure.op == "count"
                else "decimal"
                if measure.op in {"sum", "avg"}
                else kind,
                "operation": measure.op,
            }
        )
    aggregate = select(*groups, *measures, func.count().label("__rows")).select_from(filtered)
    if groups:
        aggregate = aggregate.group_by(*groups)
    aggregated = aggregate.cte("query_aggregate")
    # 单条SQL中的计数与结果共享PG快照；空分组也保留覆盖信息。
    meta = select(
        select(func.count()).select_from(source).scalar_subquery().label("__eligible"),
        select(func.count()).select_from(selected).scalar_subquery().label("__schema"),
        select(func.count()).select_from(filtered).scalar_subquery().label("__matched"),
    ).cte("query_coverage")
    statement = select(meta, aggregated).select_from(meta.outerjoin(aggregated, true()))
    orders = [
        (
            aggregated.c[o.key].asc() if o.direction == "asc" else aggregated.c[o.key].desc()
        ).nulls_last()
        for o in query.order_by
    ]
    orders += [
        aggregated.c[g.field].asc().nulls_last()
        for g in query.group_by
        if g.field not in {o.key for o in query.order_by}
    ]
    return statement.order_by(*orders).limit(query.max_groups + 1), columns


def result_rows(rows, columns, max_groups):
    require(
        len(rows) <= max_groups,
        422,
        "query_budget_exceeded",
        "分组结果超过预算；未返回截断数据，请缩小范围",
    )
    result = []
    for row in rows:
        if row["__rows"] is None:
            continue
        item = {}
        for col in columns:
            value = row[col["key"]]
            if value is not None:
                if col["type"] == "decimal":
                    value = format(value, "f")
                elif col["type"] == "integer":
                    value = int(value)
                elif col["type"] == "date":
                    value = value.isoformat()
            item[col["key"]] = value
        result.append(item)
    return result
