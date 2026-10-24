"""聚合输出显式携带范围、覆盖和列类型，不以成功响应掩盖截断。"""

from datetime import datetime
from typing import Literal

from app.common.fields import FieldType
from app.common.outputs import Output


class QueryColumn(Output):
    key: str
    type: FieldType
    operation: str


class AggregateOutput(Output):
    dataset_id: str
    schema_revision: int
    scope: Literal["own", "all"]
    source_policy: str
    eligible_records: int
    excluded_schema_records: int
    matched_records: int
    complete: Literal[True]
    columns: list[QueryColumn]
    rows: list[dict[str, str | int | bool | None]]
    read_started_at: datetime
    read_finished_at: datetime
