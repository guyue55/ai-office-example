"""官方 MCP 协议薄适配；HTTP 与工具共用应用和当前权限。"""

from typing import Literal
from uuid import uuid4

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.errors import DomainError
from app.common.metadata import SERVICE_TITLE, SERVICE_VERSION
from app.common.outputs import Associations, Page
from app.common.tracing import trace_context
from app.entrypoints.errors import DomainErrorMiddleware, protocol_http_error, tool_error_text
from app.entrypoints.mcp.validation import ContractToolError, ValidatedMCPServer
from app.modules.access.account_outputs import (
    AccountCapability,
    AccountDetail,
    AccountHistoryPage,
    AccountOperationView,
    AccountPreview,
    AccountSearchResult,
)
from app.modules.access.account_schemas import AccountRequest
from app.modules.access.change_contracts import Change
from app.modules.access.outputs import (
    AccessDetail,
    AccessListing,
    AccessPage,
    CapabilitiesOutput,
    ChangePreview,
    ChangeResult,
    IdentityOutput,
    PendingIdentityPage,
)
from app.modules.access.session_schemas import SessionPage, SessionView
from app.modules.agent.outputs import AgentCapability, RunSummary, RunView
from app.modules.data.change_contracts import DataChange, DataPreview, DataResult
from app.modules.data.outputs import (
    ConfirmationOutput,
    ConfirmedExport,
    ProjectOutput,
    RecordOutput,
    SchemaOutput,
)
from app.modules.query.outputs import AggregateOutput
from app.modules.query.schemas import AggregateQuery


def build_mcp(application):
    class Verifier:
        async def verify_token(self, token: str):
            try:
                principal = await application.verifier.verify_async(token)
                # 握手／工具目录同样要求已开通且未停用，不能仅在工具执行时检查。
                await application.call(
                    principal, "capabilities", {}, trace_context.get() or str(uuid4())
                )
                return AccessToken(
                    token=token,
                    client_id=principal.claims.get("azp", ""),
                    scopes=principal.claims.get("scope", "").split(),
                    expires_at=principal.claims["exp"],
                    resource=application.settings.audience,
                    subject=principal.subject,
                    claims=principal.claims,
                )
            except DomainError as error:
                if error.status >= 500:
                    raise
                return None

    server = ValidatedMCPServer(
        SERVICE_TITLE,
        version=SERVICE_VERSION,
        token_verifier=Verifier(),
        auth=AuthSettings(
            issuer_url=application.settings.issuer,
            resource_server_url=application.settings.audience,
            required_scopes=["office"],
            validate_token_resource=True,
        ),
    )

    async def call(operation, payload=None):
        token = get_access_token()
        if token is None:
            raise ContractToolError(
                tool_error_text(
                    DomainError(401, "login_required", "请先登录授权"), trace_context.get()
                )
            )
        # 每次工具调用重新校验令牌时效及业务权限，不信任 MCP 会话的旧授权。
        try:
            principal = await application.verifier.verify_async(token.token)
            options = {}
            if operation.startswith("agent."):
                options["authenticate"] = lambda: application.verifier.verify_async(token.token)
            return await application.call(
                principal, operation, payload or {}, trace_context.get() or str(uuid4()), **options
            )
        except DomainError as error:
            raise ContractToolError(tool_error_text(error, trace_context.get())) from None

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def aggregate_data(
        identifier: str,
        revision: int,
        query: AggregateQuery,
        scope: Literal["own", "all"] = "own",
    ) -> AggregateOutput:
        """按已发布Schema聚合。默认本人数据，all需额外权限。仅最新确认、未归档、指定结构版本；返回覆盖，不自动合并异义字段。"""
        return await call(
            "data.aggregate",
            {
                "identifier": identifier,
                "revision": revision,
                "query": query.model_dump(),
                "scope": scope,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def list_client_authorizations(
        after: str = "",
        limit: int = 20,
        scope: Literal["own", "all"] = "own",
        q: str = "",
        employee_id: str | None = None,
        state: Literal["", "active", "disabled", "revoked"] = "",
        client_id: str = "",
        archived: bool = False,
        date_field: Literal["created_at", "last_seen_at"] = "created_at",
        since: str = "",
        until: str = "",
        page: int | None = None,
    ) -> SessionPage:
        """查询本人或有权限的全企业授权。时间含时区，until不含端点；游标不能更换筛选条件。"""
        return await call(
            "session.list",
            {
                "page": page,
                "after": after,
                "limit": limit,
                "scope": scope,
                "q": q,
                "employee_id": employee_id,
                "state": state,
                "client_id": client_id,
                "archived": archived,
                "date_field": date_field,
                "since": since,
                "until": until,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def describe_client_device(
        device_name: str | None = None, platform: str | None = None
    ) -> SessionView:
        """为当前登录会话补充设备备注，仅为自报信息，不是硬件认证。"""
        return await call("session.register", {"device_name": device_name, "platform": platform})

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def change_client_authorization(
        identifier: str,
        version: int,
        action: Literal["disable", "revoke", "archive", "retry"],
        reason: str,
    ) -> SessionView:
        """用户明确确认后停用/撤销/归档授权；同次浏览器登录共享客户端一同失效。archive保留审计和拒绝记录；失败先回读，不自动重试。"""
        return await call(
            "session.change",
            {"identifier": identifier, "version": version, "action": action, "reason": reason},
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def account_capabilities() -> AccountCapability:
        """查看账号管理和邮件设密是否可用；无凭证返回。"""
        return await call("account.capabilities")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def search_login_accounts(q: str) -> AccountSearchResult:
        """按至少两个字符查找现有登录账号；仅核实资料，不按同名自动关联。"""
        return await call("account.search", {"q": q})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def employee_account(identifier: str) -> AccountDetail:
        """查询人员的业务状态和登录账号；available=false时勿猜测上游状态。"""
        return await call("account.detail", {"identifier": identifier})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def preview_account_operation(request: AccountRequest) -> AccountPreview:
        """预览开户、关联、资料修改、启停、结束登录或重置。向用户解释effects，确认后才提交。"""
        return await call("account.preview", {"request": request.model_dump()})

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def submit_account_operation(
        request: AccountRequest, confirmation_digest: str, idempotency_key: str
    ) -> AccountOperationView:
        """用户明确确认后提交。重试使用相同幂等键；查询操作直到完成。敏感凭证在next_url浏览器处理，绝不让模型接收密码。"""
        return await call(
            "account.submit",
            {
                "request": request.model_dump(),
                "confirmation_digest": confirmation_digest,
                "idempotency_key": idempotency_key,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def account_operation_history(
        identifier: str | None = None,
        after: str = "",
        limit: int = 20,
        page: int | None = None,
        q: str = "",
        status: str = "",
        kind: str = "",
        since: str = "",
        until: str = "",
    ) -> AccountHistoryPage:
        """按时间倒序分页查询账号操作；identifier可限定员工，next非空继续分页。"""
        return await call(
            "account.history",
            {
                "identifier": identifier,
                "after": after,
                "limit": limit,
                "page": page,
                "q": q,
                "status": status,
                "kind": kind,
                "since": since,
                "until": until,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def account_operation(identifier: str) -> AccountOperationView:
        """查询原操作进度。需处理时打开next_url；不要重复开户或自动重置凭证。"""
        return await call("account.operation", {"identifier": identifier})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def preview_account_resume(
        identifier: str, mode: Literal["temporary", "email"] | None = None
    ) -> AccountPreview:
        """重新核对未完成操作及当前权限；只预览，等待人工确认。"""
        return await call("account.resume_preview", {"identifier": identifier, "mode": mode})

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def resume_account_operation(
        identifier: str,
        version: int,
        confirmation_digest: str,
        mode: Literal["temporary", "email"] | None = None,
    ) -> AccountOperationView:
        """确认恢复预览后继续同一操作；不返回凭证，不重复创建已存在账号。"""
        return await call(
            "account.resume",
            {
                "identifier": identifier,
                "version": version,
                "confirmation_digest": confirmation_digest,
                "mode": mode,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def whoami() -> IdentityOutput:
        """查看当前员工身份和实时权限。"""
        return await call("me")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def capabilities() -> CapabilitiesOutput:
        """查看已实现的能力与动态字段类型，未实现能力不会声称支持。"""
        return await call("capabilities")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def list_access(
        kind: Literal["employees", "roles", "permission-sets", "permissions", "audit"],
        after: str = "",
        limit: int = 50,
        q: str = "",
        page: int | None = None,
        actor: str = "",
        action: str = "",
        since: str = "",
        until: str = "",
    ) -> AccessListing:
        """搜索人员、角色、权限集、权限目录或按追踪号查审计；next非空必须继续分页，不能猜测对象ID。"""
        return await call(
            "access.list",
            {
                "kind": kind,
                "after": after,
                "limit": limit,
                "q": q,
                "page": page,
                "actor": actor,
                "action": action,
                "since": since,
                "until": until,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def access_detail(
        kind: Literal["employees", "roles", "permission-sets"],
        identifier: str,
        after: str = "",
        limit: int = 50,
        page: int | None = None,
    ) -> AccessDetail:
        """查询配置权限的中文说明、直接关联和分页使用者；人员停用时配置权限不代表仍可访问。"""
        return await call(
            "access.detail",
            {"kind": kind, "identifier": identifier, "after": after, "limit": limit, "page": page},
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def access_links(
        kind: Literal["employees", "roles", "permission-sets"],
        identifier: str,
        relation: Literal["roles", "permission-sets", "permissions"],
    ) -> Associations:
        """读取完整直接关联和并发version；更新是完整替换，增加时须保留其他已有关联。"""
        return await call(
            "access.links.get", {"kind": kind, "identifier": identifier, "relation": relation}
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def preview_access_change(change: Change) -> ChangePreview:
        """只读预览人员绑定/启停、角色和权限集创建、授权完整替换；展示before/after、added/removed和使用者，等待用户确认。只支持已存在的身份账号绑定，不设置密码。"""
        return await call("access.change.preview", {"change": change.model_dump()})

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=False
        )
    )
    async def apply_access_change(change: Change, confirmation_digest: str) -> ChangeResult:
        """仅在用户明确确认预览后提交同一change和摘要。实时鉴权并重新核对版本；409重新预览，超时先查结果，不自动重试；成功后回读详情。"""
        return await call(
            "access.change.apply",
            {"change": change.model_dump(), "confirmation_digest": confirmation_digest},
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def pending_identities() -> PendingIdentityPage:
        """有管理权限时查询待认领登录，最多50项；不是全量分页接口。认领须预览确认。"""
        return await call("access.identity.pending")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def preview_data_change(change: DataChange) -> DataPreview:
        """预演项目创建/修改、字段发布、候选修改/归档；展示before/after，用户确认前不落盘。"""
        return await call("data.change.preview", {"change": change.model_dump(exclude_unset=True)})

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def apply_data_change(change: DataChange, confirmation_digest: str) -> DataResult:
        """明确人工确认后提交同一内容；旧版本、字段或权限变化须重新预览。禁止自动重试创建。"""
        return await call(
            "data.change.apply",
            {
                "change": change.model_dump(exclude_unset=True),
                "confirmation_digest": confirmation_digest,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def export_confirmed_records(project_id: str, max_records: int = 1000) -> ConfirmedExport:
        """单项目有界导出当前权限内的最新确认快照；超过上限报错不截断，不包含未确认候选。不是多维统计或无限批量导出。"""
        return await call(
            "record.export.confirmed", {"identifier": project_id, "max_records": max_records}
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def list_projects(after: str = "", limit: int = 50) -> Page[ProjectOutput]:
        """分页读取普通项目元数据。"""
        return await call("project.list", {"after": after, "limit": limit})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def project_schema(project_id: str, revision: int | None = None) -> SchemaOutput:
        """获取指定项目字段定义，历史版本用于解释旧记录。"""
        return await call("schema.get", {"identifier": project_id, "revision": revision})

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def submit_record(
        project_id: str, schema_revision: int, values: dict, idempotency_key: str
    ) -> RecordOutput:
        """按已发布字段定义提交候选记录；不代表已核对的业务事实。"""
        return await call(
            "record.create",
            {
                "payload": {
                    "project_id": project_id,
                    "schema_revision": schema_revision,
                    "values": values,
                    "idempotency_key": idempotency_key,
                }
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def list_records(
        project_id: str | None = None, after: str = "", limit: int = 50
    ) -> Page[RecordOutput]:
        """按当前本人或全部数据权限分页查阅上报记录。"""
        return await call("record.list", {"project_id": project_id, "after": after, "limit": limit})

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def confirm_record(record_id: str, version: int) -> ConfirmationOutput:
        """明确人工确认后冻结该工作版本；需要确认动作及记录读取权限。"""
        return await call(
            "record.confirm", {"identifier": record_id, "payload": {"version": version}}
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def correct_record(
        record_id: str, version: int, values: dict, reason: str
    ) -> RecordOutput:
        """提交更正候选，旧确认快照继续有效；不自动确认。"""
        return await call(
            "record.correct",
            {
                "identifier": record_id,
                "payload": {"version": version, "values": values, "reason": reason},
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def get_record(record_id: str) -> RecordOutput:
        """读取当前工作版本及并发version；正式取值使用confirmed_record。"""
        return await call("record.get", {"identifier": record_id})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def confirmed_record(record_id: str) -> ConfirmationOutput:
        """读取当前有效确认快照，不把未确认更正当正式数据。"""
        return await call("record.confirmed", {"identifier": record_id})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def record_history(
        record_id: str, after_revision: int = 0, limit: int = 50
    ) -> Page[ConfirmationOutput]:
        """分页查阅不可变确认历史；候选编辑日志不在此列表。"""
        return await call(
            "record.history",
            {"identifier": record_id, "after_revision": after_revision, "limit": limit},
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def assistant_capabilities() -> AgentCapability:
        """核对服务器智能助手是否配置就绪；不代表模型真实调用已验收。"""
        return await call("agent.capabilities")

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def start_assistant_run(prompt: str, request_key: str) -> RunView:
        """受理本人任务；重试保留request_key。受理不等于执行成功。"""
        return await call("agent.start", {"prompt": prompt, "request_key": request_key})

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def list_assistant_runs(
        after: str = "",
        limit: int = 10,
        page: int | None = None,
        q: str = "",
        status: str = "",
        since: str = "",
        until: str = "",
    ) -> AccessPage[RunSummary]:
        """分页查询本人任务；不读取其他员工的模型输入或结果。"""
        return await call(
            "agent.list",
            {
                "after": after,
                "limit": limit,
                "page": page,
                "q": q,
                "status": status,
                "since": since,
                "until": until,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def get_assistant_run(identifier: str) -> RunView:
        """查看本人任务的待确认预览与实际操作回执。"""
        return await call("agent.get", {"identifier": identifier})

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def decide_assistant_run(
        identifier: str, version: int, proposal_id: str, approve: bool
    ) -> RunView:
        """仅在真人核对完整预览后提交决定；不能从模型建议推断用户已同意。"""
        return await call(
            "agent.decide",
            {
                "identifier": identifier,
                "version": version,
                "proposal_id": proposal_id,
                "approve": approve,
            },
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def cancel_assistant_run(identifier: str, version: int) -> RunView:
        """请求停止本人任务；已完成的操作保留，不承诺事务回滚。"""
        return await call("agent.cancel", {"identifier": identifier, "version": version})

    subapp = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True
    )
    subapp.add_exception_handler(StarletteHTTPException, protocol_http_error)
    subapp.add_middleware(DomainErrorMiddleware)
    return server, subapp
