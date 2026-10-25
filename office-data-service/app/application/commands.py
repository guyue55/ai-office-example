"""传输无关的操作参数契约；校验发生在进入数据库事务之前。"""

from pydantic import BaseModel

from app.common.errors import DomainError
from app.common.inputs import EmptyInput, IdentifierInput, Input, ListInput
from app.modules.access.account_schemas import (
    AccountContinuePreview,
    AccountHistory,
    AccountLookup,
    AccountPreviewInput,
    AccountResume,
    AccountSearch,
    AccountSubmitInput,
)
from app.modules.access.change_contracts import ApplyChangeInput, PreviewChangeInput
from app.modules.access.schemas import ACCESS_INPUTS, normalize_access_command
from app.modules.access.session_schemas import SessionChange, SessionList, SessionRegister
from app.modules.agent.schemas import RunCancel, RunDecision, RunList, RunStart
from app.modules.data.change_contracts import DataApplyInput, DataPreviewInput
from app.modules.data.schemas import (
    ArchiveInput,
    ConfirmedExportInput,
    ConfirmInput,
    CorrectionInput,
    HistoryLookup,
    ProjectInput,
    ProjectUpdate,
    RecordInput,
    RecordList,
    RecordUpdate,
    SchemaInput,
    SchemaLookup,
)
from app.modules.query.schemas import AggregateRequest


class PayloadInput[T](Input):
    payload: T


class UpdateInput[T](IdentifierInput):
    payload: T


OPERATION_INPUTS: dict[str, type[BaseModel]] = {
    "agent.capabilities": EmptyInput,
    "agent.start": RunStart,
    "agent.list": RunList,
    "agent.get": IdentifierInput,
    "agent.decide": RunDecision,
    "agent.cancel": RunCancel,
    "data.aggregate": AggregateRequest,
    "data.change.preview": DataPreviewInput,
    "data.change.apply": DataApplyInput,
    "session.list": SessionList,
    "session.register": SessionRegister,
    "session.change": SessionChange,
    "account.history": AccountHistory,
    "account.capabilities": EmptyInput,
    "account.search": AccountSearch,
    "account.detail": AccountLookup,
    "account.preview": AccountPreviewInput,
    "account.submit": AccountSubmitInput,
    "account.operation": AccountLookup,
    "account.resume_preview": AccountContinuePreview,
    "account.resume": AccountResume,
    "me": EmptyInput,
    "capabilities": EmptyInput,
    **ACCESS_INPUTS,
    "access.change.preview": PreviewChangeInput,
    "access.change.apply": ApplyChangeInput,
    "record.export.confirmed": ConfirmedExportInput,
    "project.list": ListInput,
    "project.create": PayloadInput[ProjectInput],
    "project.update": UpdateInput[ProjectUpdate],
    "schema.get": SchemaLookup,
    "schema.publish": UpdateInput[SchemaInput],
    "record.list": RecordList,
    "record.create": PayloadInput[RecordInput],
    "record.get": IdentifierInput,
    "record.update": UpdateInput[RecordUpdate],
    "record.archive": ArchiveInput,
    "record.confirm": UpdateInput[ConfirmInput],
    "record.correct": UpdateInput[CorrectionInput],
    "record.confirmed": IdentifierInput,
    "record.history": HistoryLookup,
}


def validate_command(operation: str, payload: dict):
    if operation in ACCESS_INPUTS:
        return normalize_access_command(operation, payload)
    if operation not in OPERATION_INPUTS:
        raise DomainError(422, "unsupported_operation", "尚未提供此能力")
    result = (
        OPERATION_INPUTS[operation]
        .model_validate(payload)
        .model_dump(exclude_unset=not operation.startswith(("account.", "session.")))
    )
    return result
