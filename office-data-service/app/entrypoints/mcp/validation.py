"""SDK执行前验证原始工具参数，防止隐式转换、丢弃字段或回显输入值。"""

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import BaseModel, Field, ValidationError

from app.common.errors import DomainError, validation_error
from app.common.inputs import EmptyInput, Identifier, Input, ListInput, PageLimit, Revision
from app.common.tracing import trace_context
from app.entrypoints.errors import tool_error_text
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
from app.modules.access.schemas import AccessDetailInput, AccessList, AssociationsInput
from app.modules.access.session_schemas import SessionChange, SessionList, SessionRegister
from app.modules.data.change_contracts import DataApplyInput, DataPreviewInput
from app.modules.data.schemas import ConfirmInput, CorrectionInput, RecordInput, RecordList
from app.modules.query.schemas import AggregateRequest


class SchemaArguments(Input):
    project_id: Identifier
    revision: Revision | None = None


class ConfirmArguments(ConfirmInput):
    record_id: Identifier


class CorrectionArguments(CorrectionInput):
    record_id: Identifier


class ReadRecordArguments(Input):
    record_id: Identifier


class HistoryArguments(ReadRecordArguments):
    after_revision: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 0
    limit: PageLimit = 50


class ExportArguments(Input):
    project_id: Identifier
    max_records: Annotated[int, Field(strict=True, ge=1, le=5000)] = 1000


from app.common.inputs import IdentifierInput as ReadIdentifierArguments
from app.modules.agent.schemas import RunCancel, RunDecision, RunList, RunStart

TOOL_INPUTS: dict[str, type[BaseModel]] = {
    "assistant_capabilities": EmptyInput,
    "start_assistant_run": RunStart,
    "list_assistant_runs": RunList,
    "get_assistant_run": ReadIdentifierArguments,
    "decide_assistant_run": RunDecision,
    "cancel_assistant_run": RunCancel,
    "aggregate_data": AggregateRequest,
    "preview_data_change": DataPreviewInput,
    "apply_data_change": DataApplyInput,
    "pending_identities": EmptyInput,
    "export_confirmed_records": ExportArguments,
    "list_client_authorizations": SessionList,
    "describe_client_device": SessionRegister,
    "change_client_authorization": SessionChange,
    "account_capabilities": EmptyInput,
    "search_login_accounts": AccountSearch,
    "employee_account": AccountLookup,
    "preview_account_operation": AccountPreviewInput,
    "submit_account_operation": AccountSubmitInput,
    "account_operation": AccountLookup,
    "account_operation_history": AccountHistory,
    "preview_account_resume": AccountContinuePreview,
    "resume_account_operation": AccountResume,
    "whoami": EmptyInput,
    "capabilities": EmptyInput,
    "list_projects": ListInput,
    "project_schema": SchemaArguments,
    "submit_record": RecordInput,
    "list_records": RecordList,
    "confirm_record": ConfirmArguments,
    "correct_record": CorrectionArguments,
    "confirmed_record": ReadRecordArguments,
    "get_record": ReadRecordArguments,
    "record_history": HistoryArguments,
    "list_access": AccessList,
    "access_detail": AccessDetailInput,
    "access_links": AssociationsInput,
    "preview_access_change": PreviewChangeInput,
    "apply_access_change": ApplyChangeInput,
}


class ContractToolError(ToolError):
    """已通过业务码表生成的安全正文，允许适配层解除SDK的说明性包装。"""


class ValidatedMCPServer(MCPServer):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            if tool.name in TOOL_INPUTS:
                tool.input_schema = TOOL_INPUTS[tool.name].model_json_schema()
        return tools

    async def call_tool(self, name, arguments, context=None):
        if name not in TOOL_INPUTS:
            raise ToolError(
                tool_error_text(
                    DomainError(422, "unsupported_operation", "工具不存在"), trace_context.get()
                )
            )
        try:
            TOOL_INPUTS[name].model_validate(arguments)
        except ValidationError as error:
            raise ToolError(tool_error_text(validation_error(error), trace_context.get())) from None
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError:
            raise ToolError(
                tool_error_text(
                    DomainError(500, "internal_error", "工具执行失败，请联系管理员"),
                    trace_context.get(),
                )
            ) from None
        except ToolError as error:
            # 官方SDK给工具函数异常添加英文前缀，并通过cause保留原异常。
            # 只恢复本项目明确标记的安全错误，绝不解析任意异常文本。
            cause = error.__cause__
            while isinstance(cause, ToolError):
                if isinstance(cause, ContractToolError):
                    raise cause from None
                cause = cause.__cause__
            raise ToolError(
                tool_error_text(
                    DomainError(500, "internal_error", "工具执行失败，请联系管理员"),
                    trace_context.get(),
                )
            ) from None
