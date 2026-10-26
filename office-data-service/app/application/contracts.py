"""HTTP文档与跨协议事务内输出校验共用一份映射。"""

from pydantic import BaseModel, ValidationError

from app.common.errors import DomainError
from app.common.outputs import AccessPage, Associations, Page, Versioned
from app.modules.access.account_outputs import (
    AccountCapability,
    AccountDetail,
    AccountHistoryPage,
    AccountOperationView,
    AccountPreview,
    AccountSearchResult,
)
from app.modules.access.outputs import (
    AccessDetail,
    AuditOutput,
    CapabilitiesOutput,
    ChangePreview,
    ChangeResult,
    EmployeeListOutput,
    IdentityOutput,
    PendingIdentityPage,
    PermissionOutput,
    PermissionSetOutput,
    ProvisionedOutput,
    RoleOutput,
)
from app.modules.access.session_schemas import SessionPage, SessionView
from app.modules.agent.outputs import (
    AgentCapability,
    ConversationSummary,
    ConversationView,
    RunSummary,
    RunView,
)
from app.modules.data.change_contracts import DataPreview, DataResult
from app.modules.data.outputs import (
    ArchiveOutput,
    ConfirmationOutput,
    ConfirmedExport,
    ProjectOutput,
    RecordOutput,
    SchemaOutput,
)
from app.modules.query.outputs import AggregateOutput

OPERATION_OUTPUTS: dict[str, type[BaseModel]] = {
    "agent.conversation.create": ConversationSummary,
    "agent.conversation.list": AccessPage[ConversationSummary],
    "agent.conversation.get": ConversationView,
    "agent.conversation.archive": ConversationSummary,
    "agent.capabilities": AgentCapability,
    "agent.start": RunView,
    "agent.list": AccessPage[RunSummary],
    "agent.get": RunView,
    "agent.decide": RunView,
    "agent.cancel": RunView,
    "data.aggregate": AggregateOutput,
    "data.change.preview": DataPreview,
    "data.change.apply": DataResult,
    "session.list": SessionPage,
    "session.register": SessionView,
    "session.change": SessionView,
    "account.history": AccountHistoryPage,
    "account.capabilities": AccountCapability,
    "account.search": AccountSearchResult,
    "account.detail": AccountDetail,
    "account.preview": AccountPreview,
    "account.submit": AccountOperationView,
    "account.operation": AccountOperationView,
    "account.resume_preview": AccountPreview,
    "account.resume": AccountOperationView,
    "me": IdentityOutput,
    "capabilities": CapabilitiesOutput,
    "access.create": ProvisionedOutput,
    "access.detail": AccessDetail,
    "access.employee.update": Versioned,
    "access.links.get": Associations,
    "access.links.replace": Associations,
    "access.identity.claim": ProvisionedOutput,
    "access.identity.pending": PendingIdentityPage,
    "access.change.preview": ChangePreview,
    "access.change.apply": ChangeResult,
    "record.export.confirmed": ConfirmedExport,
    "project.list": Page[ProjectOutput],
    "project.create": ProjectOutput,
    "project.update": ProjectOutput,
    "schema.get": SchemaOutput,
    "schema.publish": SchemaOutput,
    "record.list": Page[RecordOutput],
    "record.create": RecordOutput,
    "record.get": RecordOutput,
    "record.update": RecordOutput,
    "record.archive": ArchiveOutput,
    "record.confirm": ConfirmationOutput,
    "record.correct": RecordOutput,
    "record.confirmed": ConfirmationOutput,
    "record.history": Page[ConfirmationOutput],
}
ACCESS_PAGES: dict[str, type[BaseModel]] = {
    "employees": AccessPage[EmployeeListOutput],
    "roles": AccessPage[RoleOutput],
    "permission-sets": AccessPage[PermissionSetOutput],
    "permissions": AccessPage[PermissionOutput],
    "audit": AccessPage[AuditOutput],
}
ACCESS_LIST_OUTPUT = (
    AccessPage[EmployeeListOutput]
    | AccessPage[RoleOutput]
    | AccessPage[PermissionSetOutput]
    | AccessPage[PermissionOutput]
    | AccessPage[AuditOutput]
)


def validate_output(operation: str, payload: dict, result: dict):
    model = (
        ACCESS_PAGES[payload["kind"]]
        if operation == "access.list"
        else OPERATION_OUTPUTS[operation]
    )
    try:
        return model.model_validate(result).model_dump()
    except ValidationError:
        raise DomainError(500, "invalid_response", "服务返回结构异常，请联系管理员") from None
