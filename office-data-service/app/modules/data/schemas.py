"""元数据限定已实现类型；新字段不需要更改接口或拼接 SQL。"""

from typing import Annotated

from pydantic import Field, model_validator

from app.common.fields import FieldDefinition, validate_values
from app.common.inputs import Identifier, IdentifierInput, Input, ListInput, PageLimit, Revision

__all__ = ["FieldDefinition", "validate_values"]


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)


class ProjectUpdate(Input):
    version: Revision
    name: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None

    @model_validator(mode="after")
    def explicit_changes(self):
        changes = self.model_fields_set - {"version"}
        if not changes or any(getattr(self, key) is None for key in changes):
            raise ValueError("至少提供一个非空变更字段")
        return self


class SchemaInput(Input):
    expected_revision: int = Field(ge=0, le=2147483647)
    fields: list[FieldDefinition] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({field.id for field in self.fields}) != len(self.fields):
            raise ValueError("字段标识不能重复")
        return self


class RecordInput(Input):
    project_id: str = Field(min_length=1, max_length=36)
    schema_revision: Revision
    values: dict = Field(max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)


class RecordUpdate(Input):
    version: Revision
    values: dict = Field(max_length=100)


class ConfirmInput(Input):
    version: Revision


class CorrectionInput(RecordUpdate):
    reason: str = Field(min_length=1, max_length=1000)


class SchemaLookup(IdentifierInput):
    revision: Revision | None = None


class RecordList(ListInput):
    project_id: Identifier | None = None


class ArchiveInput(IdentifierInput):
    version: Revision


class HistoryLookup(IdentifierInput):
    after_revision: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 0
    limit: PageLimit = 50


class ConfirmedExportInput(IdentifierInput):
    max_records: Annotated[int, Field(strict=True, ge=1, le=5000)] = 1000
