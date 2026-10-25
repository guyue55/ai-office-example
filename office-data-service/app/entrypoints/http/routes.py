"""HTTP 仅负责输入与输出适配；全站单轨v2契约，无旧格式分支。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from office_contracts import Failure, Meta, prepare_success
from office_contracts.integrations.fastapi import ContractRouter
from pydantic import ConfigDict, Field

from app.application.codebook import aliases
from app.application.contracts import ACCESS_LIST_OUTPUT, OPERATION_OUTPUTS
from app.common.errors import DomainError
from app.modules.access.account_schemas import (
    AccountContinuePreview,
    AccountPreviewInput,
    AccountResume,
    AccountSubmitInput,
)
from app.modules.access.authentication import Principal
from app.modules.access.change_contracts import ApplyChangeInput, PreviewChangeInput
from app.modules.access.schemas import EmployeeUpdate, GrantUpdate, Links, Named, NewEmployee
from app.modules.access.session_schemas import SessionChange, SessionList, SessionRegister
from app.modules.agent.schemas import RunCancel, RunDecision, RunList, RunStart
from app.modules.data.change_contracts import DataApplyInput, DataPreviewInput
from app.modules.data.schemas import (
    ConfirmInput,
    CorrectionInput,
    ProjectInput,
    ProjectUpdate,
    RecordInput,
    RecordUpdate,
    SchemaInput,
)
from app.modules.query.schemas import AggregateRequest


class SessionQuery(SessionList):
    # URL查询参数天然为文本；仅传输适配允许转换，应用层仍严格验证。
    model_config = ConfigDict(extra="forbid", strict=False, str_strip_whitespace=True)
    limit: Annotated[int, Field(ge=1, le=100, strict=False)] = 20
    page: Annotated[int, Field(ge=1, le=2147483647, strict=False)] | None = None


class AgentQuery(RunList):
    model_config = ConfigDict(extra="forbid", strict=False, str_strip_whitespace=True)
    limit: Annotated[int, Field(ge=1, le=100, strict=False)] = 10
    page: Annotated[int, Field(ge=1, le=2147483647, strict=False)] | None = None


Kind = Literal["employees", "roles", "permission-sets"]


def router(application, *, registry, prefix="/api/v2"):
    def group(**kwargs):
        return ContractRouter(
            registry=registry,
            default_errors=tuple(set(aliases().values())),
            **kwargs,
        )

    api = APIRouter(
        prefix=prefix,
        responses={
            status: {"model": Failure}
            for status in (400, 401, 403, 404, 405, 408, 409, 413, 415, 422, 500, 503)
        },
    )
    identity = group(tags=["身份与能力"])
    access = group(tags=["用户与权限"])
    data = group(tags=["项目与数据"])

    async def actor(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
        ],
    ):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise DomainError(401, "login_required", "请先通过浏览器登录授权")
        return await application.verifier.verify_async(credentials.credentials)

    Actor = Annotated[Principal, Depends(actor)]

    async def invoke(request, principal, operation, payload=None):
        allowed = {
            "agent.list": set(RunList.model_fields),
            "session.list": set(SessionList.model_fields),
            "access.list": {"after", "limit", "q", "page", "actor", "action", "since", "until"},
            "account.search": {"q"},
            "account.history": {
                "identifier",
                "after",
                "limit",
                "page",
                "q",
                "status",
                "kind",
                "since",
                "until",
            },
            "access.detail": {"after", "limit", "page"},
            "project.list": {"after", "limit"},
            "record.export.confirmed": {"max_records"},
            "record.list": {"after", "limit", "project_id"},
            "schema.get": {"revision"},
            "record.archive": {"version"},
            "record.history": {"after_revision", "limit"},
        }.get(operation, set())
        keys = [key for key, value in request.query_params.multi_items()]
        if set(keys) - allowed or len(keys) != len(set(keys)):
            raise DomainError(422, "invalid_query", "查询参数未定义或重复，请核对接口说明")
        model = ACCESS_LIST_OUTPUT if operation == "access.list" else OPERATION_OUTPUTS[operation]

        def present(result, *, replayed=False):
            return prepare_success(
                result,
                model,
                Meta(trace_id=request.state.trace_id, replayed=replayed),
                registry=registry,
            )

        options = {}
        if operation.startswith("agent."):
            raw_token = request.headers.get("authorization", "").partition(" ")[2]
            options["authenticate"] = lambda: application.verifier.verify_async(raw_token)
        return await application.call(
            principal,
            operation,
            payload or {},
            request.state.trace_id,
            presenter=present,
            **options,
        )

    agent = group(tags=["智能助手"])

    @agent.get("/agent/capabilities", response_model=OPERATION_OUTPUTS["agent.capabilities"])
    async def assistant_capabilities(request: Request, principal: Actor):
        return await invoke(request, principal, "agent.capabilities")

    @agent.post("/agent/runs", status_code=202, response_model=OPERATION_OUTPUTS["agent.start"])
    async def assistant_start(request: Request, args: RunStart, principal: Actor):
        return await invoke(request, principal, "agent.start", args.model_dump())

    @agent.get("/agent/runs", response_model=OPERATION_OUTPUTS["agent.list"])
    async def assistant_runs(
        request: Request, principal: Actor, args: Annotated[AgentQuery, Query()]
    ):
        return await invoke(request, principal, "agent.list", args.model_dump())

    @agent.get("/agent/runs/{identifier}", response_model=OPERATION_OUTPUTS["agent.get"])
    async def assistant_run(request: Request, identifier: str, principal: Actor):
        return await invoke(request, principal, "agent.get", {"identifier": identifier})

    @agent.post("/agent/runs/decision", response_model=OPERATION_OUTPUTS["agent.decide"])
    async def assistant_decide(request: Request, args: RunDecision, principal: Actor):
        return await invoke(request, principal, "agent.decide", args.model_dump())

    @agent.post("/agent/runs/cancel", response_model=OPERATION_OUTPUTS["agent.cancel"])
    async def assistant_cancel(request: Request, args: RunCancel, principal: Actor):
        return await invoke(request, principal, "agent.cancel", args.model_dump())

    @data.post("/data/aggregate", response_model=OPERATION_OUTPUTS["data.aggregate"])
    async def aggregate(body: AggregateRequest, request: Request, *, principal: Actor):
        return await invoke(request, principal, "data.aggregate", body.model_dump())

    @identity.get("/client-authorizations", response_model=OPERATION_OUTPUTS["session.list"])
    async def session_list(
        request: Request, filters: Annotated[SessionQuery, Query()], *, principal: Actor
    ):
        return await invoke(request, principal, "session.list", filters.model_dump())

    @identity.post(
        "/client-authorizations/current", response_model=OPERATION_OUTPUTS["session.register"]
    )
    async def session_register(body: SessionRegister, request: Request, *, principal: Actor):
        return await invoke(request, principal, "session.register", body.model_dump())

    @identity.post(
        "/client-authorizations/change", response_model=OPERATION_OUTPUTS["session.change"]
    )
    async def session_change(body: SessionChange, request: Request, *, principal: Actor):
        return await invoke(request, principal, "session.change", body.model_dump())

    @identity.get("/me", response_model=OPERATION_OUTPUTS["me"])
    async def me(request: Request, *, principal: Actor):
        return await invoke(request, principal, "me")

    @identity.get("/capabilities", response_model=OPERATION_OUTPUTS["capabilities"])
    async def capabilities(request: Request, *, principal: Actor):
        return await invoke(request, principal, "capabilities")

    @access.get("/account-operations", response_model=OPERATION_OUTPUTS["account.history"])
    async def account_history(
        request: Request,
        identifier: str | None = None,
        after: str = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        page: Annotated[int | None, Query(ge=1)] = None,
        q: str = "",
        status: str = "",
        kind: str = "",
        since: str = "",
        until: str = "",
        *,
        principal: Actor,
    ):
        return await invoke(
            request,
            principal,
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

    @access.get("/accounts/capabilities", response_model=OPERATION_OUTPUTS["account.capabilities"])
    async def account_capabilities(request: Request, *, principal: Actor):
        return await invoke(request, principal, "account.capabilities")

    @access.get("/accounts/search", response_model=OPERATION_OUTPUTS["account.search"])
    async def account_search(
        request: Request,
        q: Annotated[str, Query(min_length=2, max_length=100)],
        *,
        principal: Actor,
    ):
        return await invoke(request, principal, "account.search", {"q": q})

    @access.get(
        "/accounts/employees/{identifier}", response_model=OPERATION_OUTPUTS["account.detail"]
    )
    async def account_detail(identifier: str, request: Request, *, principal: Actor):
        return await invoke(request, principal, "account.detail", {"identifier": identifier})

    @access.post("/account-operations/preview", response_model=OPERATION_OUTPUTS["account.preview"])
    async def account_preview(body: AccountPreviewInput, request: Request, *, principal: Actor):
        return await invoke(request, principal, "account.preview", body.model_dump())

    @access.post(
        "/account-operations", status_code=202, response_model=OPERATION_OUTPUTS["account.submit"]
    )
    async def account_submit(body: AccountSubmitInput, request: Request, *, principal: Actor):
        return await invoke(request, principal, "account.submit", body.model_dump())

    @access.get(
        "/account-operations/{identifier}", response_model=OPERATION_OUTPUTS["account.operation"]
    )
    async def account_operation(identifier: str, request: Request, *, principal: Actor):
        return await invoke(request, principal, "account.operation", {"identifier": identifier})

    @access.post(
        "/account-operations/{identifier}/resume-preview",
        response_model=OPERATION_OUTPUTS["account.resume_preview"],
    )
    async def account_resume_preview(
        identifier: str, body: AccountContinuePreview, request: Request, *, principal: Actor
    ):
        if body.identifier != identifier:
            raise DomainError(422, "invalid_input", "操作编号不一致")
        return await invoke(request, principal, "account.resume_preview", body.model_dump())

    @access.post(
        "/account-operations/{identifier}/resume",
        response_model=OPERATION_OUTPUTS["account.resume"],
    )
    async def account_resume(
        identifier: str, body: AccountResume, request: Request, *, principal: Actor
    ):
        if body.identifier != identifier:
            raise DomainError(422, "invalid_input", "操作编号不一致")
        return await invoke(request, principal, "account.resume", body.model_dump())

    @access.get("/access/{kind}", response_model=ACCESS_LIST_OUTPUT)
    async def access_list(
        kind: Literal["employees", "roles", "permission-sets", "permissions", "audit"],
        request: Request,
        after: str = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        q: Annotated[str, Query(max_length=100)] = "",
        page: Annotated[int | None, Query(ge=1)] = None,
        actor: str = "",
        action: str = "",
        since: str = "",
        until: str = "",
        *,
        principal: Actor,
    ):
        return await invoke(
            request,
            principal,
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

    @data.get(
        "/projects/{identifier}/confirmed-export",
        response_model=OPERATION_OUTPUTS["record.export.confirmed"],
    )
    async def confirmed_export(
        identifier: str,
        request: Request,
        max_records: Annotated[int, Query(ge=1, le=5000)] = 1000,
        *,
        principal: Actor,
    ):
        return await invoke(
            request,
            principal,
            "record.export.confirmed",
            {"identifier": identifier, "max_records": max_records},
        )

    @data.post("/data-changes/preview", response_model=OPERATION_OUTPUTS["data.change.preview"])
    async def data_change_preview(body: DataPreviewInput, request: Request, *, principal: Actor):
        return await invoke(
            request, principal, "data.change.preview", body.model_dump(exclude_unset=True)
        )

    @data.post("/data-changes/apply", response_model=OPERATION_OUTPUTS["data.change.apply"])
    async def data_change_apply(body: DataApplyInput, request: Request, *, principal: Actor):
        return await invoke(
            request, principal, "data.change.apply", body.model_dump(exclude_unset=True)
        )

    @access.get("/pending-identities", response_model=OPERATION_OUTPUTS["access.identity.pending"])
    async def pending_identities(request: Request, *, principal: Actor):
        return await invoke(request, principal, "access.identity.pending")

    @access.post(
        "/access-changes/preview", response_model=OPERATION_OUTPUTS["access.change.preview"]
    )
    async def change_preview(body: PreviewChangeInput, request: Request, *, principal: Actor):
        return await invoke(request, principal, "access.change.preview", body.model_dump())

    @access.post("/access-changes/apply", response_model=OPERATION_OUTPUTS["access.change.apply"])
    async def change_apply(body: ApplyChangeInput, request: Request, *, principal: Actor):
        return await invoke(request, principal, "access.change.apply", body.model_dump())

    @access.get("/access/{kind}/{identifier}", response_model=OPERATION_OUTPUTS["access.detail"])
    async def access_detail(
        kind: Kind,
        identifier: str,
        request: Request,
        after: str = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        page: Annotated[int | None, Query(ge=1)] = None,
        *,
        principal: Actor,
    ):
        return await invoke(
            request,
            principal,
            "access.detail",
            {"kind": kind, "identifier": identifier, "after": after, "limit": limit, "page": page},
        )

    @access.post(
        "/access/{kind}", status_code=201, response_model=OPERATION_OUTPUTS["access.create"]
    )
    async def access_create(
        kind: Kind, body: NewEmployee | Named, request: Request, *, principal: Actor
    ):
        return await invoke(
            request, principal, "access.create", {"kind": kind, "body": body.model_dump()}
        )

    @access.patch(
        "/access/employees/{identifier}", response_model=OPERATION_OUTPUTS["access.employee.update"]
    )
    async def employee_update(
        identifier: str, body: EmployeeUpdate, request: Request, *, principal: Actor
    ):
        return await invoke(
            request,
            principal,
            "access.employee.update",
            {"identifier": identifier, "body": body.model_dump()},
        )

    @access.get(
        "/access/{kind}/{identifier}/{relation}",
        response_model=OPERATION_OUTPUTS["access.links.get"],
    )
    async def links_get(
        kind: Kind, identifier: str, relation: str, request: Request, *, principal: Actor
    ):
        check_relation(kind, relation)
        return await invoke(
            request,
            principal,
            "access.links.get",
            {"kind": kind, "identifier": identifier, "relation": relation},
        )

    @access.put(
        "/access/{kind}/{identifier}/{relation}",
        response_model=OPERATION_OUTPUTS["access.links.replace"],
    )
    async def links_replace(
        kind: Kind,
        identifier: str,
        relation: str,
        body: Links | GrantUpdate,
        request: Request,
        *,
        principal: Actor,
    ):
        check_relation(kind, relation)
        return await invoke(
            request,
            principal,
            "access.links.replace",
            {
                "kind": kind,
                "identifier": identifier,
                "relation": relation,
                "body": body.model_dump(),
            },
        )

    @data.get("/projects", response_model=OPERATION_OUTPUTS["project.list"])
    async def projects(
        request: Request,
        after: str = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        *,
        principal: Actor,
    ):
        return await invoke(request, principal, "project.list", {"after": after, "limit": limit})

    @data.post("/projects", status_code=201, response_model=OPERATION_OUTPUTS["project.create"])
    async def project_create(body: ProjectInput, request: Request, *, principal: Actor):
        return await invoke(request, principal, "project.create", {"payload": body.model_dump()})

    @data.patch("/projects/{identifier}", response_model=OPERATION_OUTPUTS["project.update"])
    async def project_update(
        identifier: str, body: ProjectUpdate, request: Request, *, principal: Actor
    ):
        return await invoke(
            request,
            principal,
            "project.update",
            {"identifier": identifier, "payload": body.model_dump(exclude_unset=True)},
        )

    @data.get("/projects/{identifier}/schema", response_model=OPERATION_OUTPUTS["schema.get"])
    async def schema(
        identifier: str, request: Request, revision: int | None = None, *, principal: Actor
    ):
        result = await invoke(
            request, principal, "schema.get", {"identifier": identifier, "revision": revision}
        )
        return result

    @data.post(
        "/projects/{identifier}/schema",
        status_code=201,
        response_model=OPERATION_OUTPUTS["schema.publish"],
    )
    async def schema_publish(
        identifier: str, body: SchemaInput, request: Request, *, principal: Actor
    ):
        return await invoke(
            request,
            principal,
            "schema.publish",
            {"identifier": identifier, "payload": body.model_dump()},
        )

    @data.get("/records", response_model=OPERATION_OUTPUTS["record.list"])
    async def records(
        request: Request,
        project_id: str | None = None,
        after: str = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        *,
        principal: Actor,
    ):
        return await invoke(
            request,
            principal,
            "record.list",
            {"project_id": project_id, "after": after, "limit": limit},
        )

    @data.post("/records", status_code=201, response_model=OPERATION_OUTPUTS["record.create"])
    async def record_create(body: RecordInput, request: Request, *, principal: Actor):
        return await invoke(request, principal, "record.create", {"payload": body.model_dump()})

    @data.get("/records/{identifier}", response_model=OPERATION_OUTPUTS["record.get"])
    async def record_get(identifier: str, request: Request, *, principal: Actor):
        return await invoke(request, principal, "record.get", {"identifier": identifier})

    @data.patch("/records/{identifier}", response_model=OPERATION_OUTPUTS["record.update"])
    async def record_update(
        identifier: str, body: RecordUpdate, request: Request, *, principal: Actor
    ):
        return await invoke(
            request,
            principal,
            "record.update",
            {"identifier": identifier, "payload": body.model_dump()},
        )

    @data.delete("/records/{identifier}", response_model=OPERATION_OUTPUTS["record.archive"])
    async def record_archive(identifier: str, version: int, request: Request, *, principal: Actor):
        return await invoke(
            request, principal, "record.archive", {"identifier": identifier, "version": version}
        )

    @data.post("/records/{identifier}/confirm", response_model=OPERATION_OUTPUTS["record.confirm"])
    async def confirm_record(
        identifier: str, body: ConfirmInput, request: Request, *, principal: Actor
    ):
        return await invoke(
            request,
            principal,
            "record.confirm",
            {"identifier": identifier, "payload": body.model_dump()},
        )

    @data.post(
        "/records/{identifier}/corrections", response_model=OPERATION_OUTPUTS["record.correct"]
    )
    async def correct_record(
        identifier: str, body: CorrectionInput, request: Request, *, principal: Actor
    ):
        return await invoke(
            request,
            principal,
            "record.correct",
            {"identifier": identifier, "payload": body.model_dump()},
        )

    @data.get(
        "/records/{identifier}/confirmed", response_model=OPERATION_OUTPUTS["record.confirmed"]
    )
    async def confirmed_record(identifier: str, request: Request, *, principal: Actor):
        return await invoke(request, principal, "record.confirmed", {"identifier": identifier})

    @data.get("/records/{identifier}/history", response_model=OPERATION_OUTPUTS["record.history"])
    async def record_history(
        identifier: str,
        request: Request,
        after_revision: int = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        *,
        principal: Actor,
    ):
        return await invoke(
            request,
            principal,
            "record.history",
            {"identifier": identifier, "after_revision": after_revision, "limit": limit},
        )

    api.include_router(identity)
    api.include_router(agent)
    api.include_router(access)
    api.include_router(data)
    return api


def check_relation(kind, relation):
    if (kind, relation) not in {
        ("employees", "roles"),
        ("roles", "permission-sets"),
        ("permission-sets", "permissions"),
    }:
        raise DomainError(404, "not_found", "关联资源不存在")
