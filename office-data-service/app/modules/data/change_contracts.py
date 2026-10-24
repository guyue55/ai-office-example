"""数据管理变更的封闭集合，不允许任意应用操作或HTTP路径。"""

from typing import Literal

from pydantic import Field, field_serializer

from app.common.inputs import IdentifierInput, Input
from app.common.outputs import Output
from app.modules.data.schemas import (
    ArchiveInput,
    ProjectInput,
    ProjectUpdate,
    RecordUpdate,
    SchemaInput,
)


class ProjectCreate(Input):
    operation: Literal["project.create"]
    payload: ProjectInput


class ProjectEdit(IdentifierInput):
    payload: ProjectUpdate


class SchemaEdit(IdentifierInput):
    payload: SchemaInput


class RecordEdit(IdentifierInput):
    payload: RecordUpdate


class ProjectChange(Input):
    operation: Literal["project.update"]
    payload: ProjectEdit


class SchemaChange(Input):
    operation: Literal["schema.publish"]
    payload: SchemaEdit


class RecordChange(Input):
    operation: Literal["record.update"]
    payload: RecordEdit


class ArchiveChange(Input):
    operation: Literal["record.archive"]
    payload: ArchiveInput


DataChange = ProjectCreate | ProjectChange | SchemaChange | RecordChange | ArchiveChange


class DataPreviewInput(Input):
    change: DataChange


class DataApplyInput(DataPreviewInput):
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DataPreview(Output):
    change: DataChange
    confirmation_digest: str
    before: dict
    after: dict
    notice: str

    @field_serializer("change")
    def serialize_change(self, change):
        # 未指定的可选变更字段不能在预览响应中变成显式null，回传同一change必须有效。
        return change.model_dump(exclude_unset=True)


class DataResult(Output):
    operation: Literal[
        "project.create", "project.update", "schema.publish", "record.update", "record.archive"
    ]
    result: dict
