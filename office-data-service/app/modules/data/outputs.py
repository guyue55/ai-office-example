"""项目数据输出保持动态值，不把某个项目的业务字段固化进接口。"""

from datetime import datetime
from typing import Literal

from app.common.outputs import Output, Versioned
from app.modules.data.schemas import FieldDefinition


class ProjectOutput(Versioned):
    name: str
    archived: bool
    schema_revision: int


class SchemaOutput(Output):
    project_id: str
    revision: int
    fields: list[FieldDefinition]


class RecordOutput(Versioned):
    project_id: str
    schema_revision: int
    owner_id: str
    values: dict
    archived: bool
    status: Literal["submitted", "confirmed"]


class ArchiveOutput(Versioned):
    archived: Literal[True]


class ConfirmationOutput(Output):
    record_id: str
    revision: int
    schema_revision: int
    values: dict
    confirmed_by: str
    confirmed_at: datetime
    reason: str | None


class ConfirmedExport(Output):
    scope: Literal["own", "all"]
    project_id: str
    read_started_at: datetime
    read_finished_at: datetime
    items: list[ConfirmationOutput]
