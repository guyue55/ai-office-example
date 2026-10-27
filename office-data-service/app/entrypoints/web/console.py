"""服务端Web控制台：复用应用服务，助手执行由可选协调器负责。"""

import base64
import hashlib
import hmac
import logging
import re
import time
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.common.errors import DomainError, log_rejection, validation_error
from app.entrypoints.errors import error_response
from app.entrypoints.web.accounts import register_accounts
from app.entrypoints.web.assistant import OPERATIONS, OUTCOMES, STATUSES, register_assistant
from app.entrypoints.web.assistant_ui import register_assistant_ui
from app.entrypoints.web.authentication import WebAuthentication, bind_identity
from app.entrypoints.web.client_sessions import register_client_sessions
from app.entrypoints.web.login_gate import LoginGate
from app.entrypoints.web.responses import COOKIE, LOGIN_COOKIE, TEMPLATES, web_error_response
from app.entrypoints.web.session import LoginRequired, SessionStore, new_csrf
from app.entrypoints.web.tables import table_query
from app.modules.access.catalog import (
    ACCOUNT_ACTIONS,
    ACCOUNT_STATUSES,
    AUDIT_ACTIONS,
    describe_permission,
)
from app.modules.access.schemas import AccessList

LOGIN_COOKIE_PREFIX = LOGIN_COOKIE + "_"
LOGIN_COOKIE_LIMIT = 8
ACCESS_LABELS = {
    "employees": "人员",
    "roles": "角色",
    "permission-sets": "权限集",
    "permissions": "权限",
}


def login_cookie_name(state: str) -> str:
    """state由服务端生成，Cookie按事务隔离，避免标签页覆盖彼此的浏览器绑定。"""
    return LOGIN_COOKIE_PREFIX + state


def web_router(application):
    settings = application.settings
    store = SessionStore(
        settings.web_session_secret,
        settings.web_session_ttl,
        settings.web_session_limit,
        settings.web_pending_limit,
    )

    login_gate = LoginGate(settings.web_login_protection)
    authentication = WebAuthentication(application, store)

    class ConsoleRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()

            async def guarded(request: Request):
                try:
                    return await handler(request)
                except LoginRequired:
                    error = DomainError(401, "login_required", "请先登录")
                except DomainError as caught:
                    error = caught
                except (ValidationError, RequestValidationError) as caught:
                    error = validation_error(caught)
                if error.status == 401:
                    store.destroy(request.cookies.get(COOKIE))
                log_rejection(request.state.trace_id, error.code, error.status, "web")
                if request.url.path.startswith("/console/assistant-ui/"):
                    response = error_response(error, request.state.trace_id)
                    response.headers["Cache-Control"] = "no-store"
                    return response
                return web_error_response(request, error)

            return guarded

    # 表单／回调属于页面入口，业务 OpenAPI 继续由 /api/v2 声明。
    router = APIRouter(route_class=ConsoleRoute, include_in_schema=False)

    async def token_request(code: str, verifier: str) -> dict:
        token_url = settings.issuer + "/protocol/openid-connect/token"
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.public_url + "/auth/callback",
            "client_id": settings.web_client_id,
            "code_verifier": verifier,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(token_url, data=data)
        response.raise_for_status()
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("token_type"), str)
            or payload["token_type"].lower() != "bearer"
            or not isinstance(payload.get("access_token"), str)
            or not payload["access_token"]
        ):
            raise DomainError(503, "identity_unavailable", "身份服务返回无效")
        return payload

    async def principal_of(request: Request):
        return await authentication.principal(request.cookies.get(COOKIE))

    async def render(request, template, **context):
        token = store.load(request.cookies.get(COOKIE))
        principal = await principal_of(request)
        identity = await application.call(principal, "me", {}, request.state.trace_id)
        context.update(
            me=identity,
            page_template=template,
            assistant_statuses=STATUSES,
            assistant_outcomes=OUTCOMES,
            assistant_operations=OPERATIONS,
            demo_mode=settings.allow_lan_http_demo,
            csrf=token.get("csrf", ""),
            trace_id=request.state.trace_id,
            can_manage="access.manage" in identity["permissions"],
            can_update_employee=bool(
                {"employee.update", "access.manage"} & set(identity["permissions"])
            ),
            permission_details=[describe_permission(code) for code in identity["permissions"]],
            access_labels=ACCESS_LABELS,
            audit_actions=AUDIT_ACTIONS,
            filter_mode={
                "audit.html": "audit",
                "client_authorizations.html": "authorizations",
                "account_operations.html": "operations",
            }.get(template, ""),
            account_actions=ACCOUNT_ACTIONS,
            account_statuses=ACCOUNT_STATUSES,
            notice=token.pop("notice", None),
        )
        return TEMPLATES.TemplateResponse(request, template, context)

    async def operate(request, operation: str, payload: dict | None = None):
        try:
            principal = await principal_of(request)
        except LoginRequired:
            raise DomainError(401, "login_required", "请先登录") from None
        options = {}
        if operation.startswith("agent."):
            cookie = request.cookies.get(COOKIE)
            options["authenticate"] = lambda: authentication.principal(cookie)
        return await application.call(
            principal, operation, payload or {}, request.state.trace_id, **options
        )

    def require_csrf(request: Request, form: dict):
        supplied = str(form.get("csrf", ""))
        session = store.load(request.cookies.get(COOKIE))
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise DomainError(403, "csrf_mismatch", "表单校验失败，请刷新页面重试")

    def form_version(form: dict) -> int:
        value = str(form.get("version", ""))
        if not re.fullmatch(r"[1-9][0-9]{0,9}", value) or int(value) > 2147483647:
            raise DomainError(422, "invalid_input", "页面版本无效，请刷新后重试")
        return int(value)

    def notify(request, message):
        store.load(request.cookies.get(COOKIE))["notice"] = message

    @router.get("/", include_in_schema=False)
    async def home():
        # 首页复用控制台入口，登录检查与身份授权仍由同一条链路处理。
        return RedirectResponse("/console", status_code=303)

    @router.get("/console", response_class=HTMLResponse)
    async def entry(request: Request):
        store.sweep()
        try:
            store.load(request.cookies.get(COOKIE))
        except LoginRequired:
            existing = [name for name in request.cookies if name.startswith(LOGIN_COOKIE_PREFIX)]
            active = [
                name for name in existing if name[len(LOGIN_COOKIE_PREFIX) :] in store.pending
            ]
            if len(active) >= LOGIN_COOKIE_LIMIT:
                raise DomainError(
                    503, "authentication_busy", "待完成的登录过多，请先完成登录或等待十分钟后重试"
                )
            source = login_gate.admit_request(request, store)
            state = "st_" + new_csrf()
            verifier = new_csrf() + new_csrf()
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            binding = new_csrf()
            nonce = new_csrf()
            pending = {
                "verifier": verifier,
                "csrf": new_csrf(),
                "binding": binding,
                "nonce": nonce,
                "_source": source,
            }
            store.put_pending(state, pending)
            authorize = settings.issuer + "/protocol/openid-connect/auth"
            params = {
                "client_id": settings.web_client_id,
                "redirect_uri": settings.public_url + "/auth/callback",
                "response_type": "code",
                "scope": "openid office",
                "nonce": nonce,
                "max_age": str(settings.web_authentication_max_age),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            response = RedirectResponse(authorize + "?" + urlencode(params), status_code=303)
            for name in existing:
                if name not in active:
                    response.delete_cookie(name, path="/")
            # 清理旧版本共享Cookie；新事务不接受它作为绑定凭据。
            response.delete_cookie(LOGIN_COOKIE, path="/auth/callback")
            response.set_cookie(
                login_cookie_name(state),
                binding,
                max_age=600,
                path="/",
                httponly=True,
                samesite="lax",
                secure=settings.public_url.startswith("https://"),
            )
            return response
        return RedirectResponse("/console/dashboard", status_code=303)

    @router.get("/auth/callback", response_class=HTMLResponse)
    async def callback(request: Request, code: str = "", state: str = ""):
        pending = store.pending.get(state)
        if pending is None:
            return HTMLResponse("登录状态失效，请重新打开控制台。", status_code=400)
        pending_value, deadline = pending
        if not hmac.compare_digest(
            request.cookies.get(login_cookie_name(state), "").encode(),
            pending_value["binding"].encode(),
        ):
            raise DomainError(400, "invalid_request", "登录来源不匹配，请重新打开控制台")
        # 校验浏览器后消费，防重放；错误浏览器不能消耗其他人的登录请求。
        store.pending.pop(state)
        if time.monotonic() > deadline:
            return HTMLResponse("登录超时，请重新打开控制台。", status_code=400)
        try:
            token = await token_request(code, pending_value["verifier"])
            verified = await application.verifier.verify_async(token["access_token"])
            proof = await application.verifier.verify_id_async(
                token.get("id_token", ""), pending_value["nonce"]
            )
            if proof.subject != verified.subject:
                raise DomainError(401, "invalid_token", "身份令牌不匹配，请重新登录")
            if not pending_value.get("purpose"):
                authenticated = proof.claims.get("auth_time")
                if type(authenticated) is not int or not (
                    time.time() - settings.web_authentication_max_age - 5
                    <= authenticated
                    <= time.time() + 5
                ):
                    raise DomainError(401, "invalid_token", "需要重新验证本人身份")
            if pending_value.get("purpose"):
                original = pending_value["subject"]
                if verified.subject != original:
                    raise DomainError(403, "permission_denied", "认证账号已变化，请使用原账号重试")
                if (
                    proof.subject != original
                    or type(proof.claims.get("auth_time")) is not int
                    or proof.claims["auth_time"] < pending_value["started"] - 5
                ):
                    raise DomainError(401, "invalid_token", "需要重新验证本人身份")
                token["recent_auth"] = time.time()
                token["credential_operation"] = pending_value.get("operation")

        except (httpx.HTTPError, ValueError, DomainError):
            logging.getLogger("office.service").warning(
                "[Trace: OfficeService/Web/token_exchange_failed] trace_id=%s",
                request.state.trace_id,
            )
            return HTMLResponse("登录失败，请重新打开控制台重试。", status_code=502)
        bind_identity(token, verified)
        token["csrf"] = pending_value["csrf"]
        store.destroy(request.cookies.get(COOKIE))
        cookie_value = store.create(token)
        destination = "/console/dashboard"
        if pending_value.get("purpose") == "credential":
            destination = "/console/accounts/operations/" + quote(
                pending_value["operation"], safe=""
            )
        elif pending_value.get("purpose") == "password":
            destination = "/console/security?returned=1"
        response = RedirectResponse(destination, status_code=303)
        response.set_cookie(
            COOKIE,
            cookie_value,
            max_age=settings.web_session_ttl,
            path="/",
            httponly=True,
            samesite="lax",
            secure=settings.public_url.startswith("https://"),
        )
        response.delete_cookie(login_cookie_name(state), path="/")
        return response

    @router.get("/console/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return await render(request, "dashboard.html")

    async def access_listing(request, kind, template, after, q):
        listing = await operate(
            request,
            "access.list",
            {"kind": kind, **table_query(request, set(AccessList.model_fields) - {"kind"})},
        )
        items = listing["items"]
        if kind == "permissions":
            items = [describe_permission(row["code"], row["description"]) for row in items]
        return await render(request, template, items=items, listing=listing, search=q)

    @router.get("/console/employees", response_class=HTMLResponse)
    async def employees(request: Request, after: str = "", q: str = ""):
        listing = await operate(
            request,
            "access.list",
            {"kind": "employees", **table_query(request, set(AccessList.model_fields) - {"kind"})},
        )
        identity = await operate(request, "me", {})
        pending = (
            await operate(request, "access.identity.pending", {})
            if "access.manage" in identity["permissions"]
            else None
        )
        return await render(
            request,
            "employees.html",
            items=listing["items"],
            listing=listing,
            search=q,
            pending=pending,
            accounts_enabled=settings.identity_management is not None,
        )

    @router.get("/console/roles", response_class=HTMLResponse)
    async def roles(request: Request, after: str = "", q: str = ""):
        return await access_listing(request, "roles", "roles.html", after, q)

    @router.get("/console/permission-sets", response_class=HTMLResponse)
    async def permission_sets(request: Request, after: str = "", q: str = ""):
        return await access_listing(request, "permission-sets", "permission_sets.html", after, q)

    @router.get("/console/permissions", response_class=HTMLResponse)
    async def permissions(request: Request, after: str = "", q: str = ""):
        return await access_listing(request, "permissions", "permissions.html", after, q)

    @router.get("/console/access/{kind}/{identifier}", response_class=HTMLResponse)
    async def access_detail(request: Request, kind: str, identifier: str, after: str = ""):
        if kind not in {"employees", "roles", "permission-sets"}:
            raise DomainError(404, "not_found", "授权对象不存在")
        detail = await operate(
            request,
            "access.detail",
            {
                "kind": kind,
                "identifier": identifier,
                **table_query(request, {"after", "limit", "page"}),
            },
        )
        return await render(
            request, "access_detail.html", detail=detail, kind=kind, listing=detail["used_by"]
        )

    @router.get("/console/audit", response_class=HTMLResponse)
    async def audit(request: Request, after: str = "", q: str = ""):
        listing = await operate(
            request,
            "access.list",
            {"kind": "audit", **table_query(request, set(AccessList.model_fields) - {"kind"})},
        )
        return await render(
            request,
            "audit.html",
            items=listing["items"],
            listing=listing,
            search=q,
            search_placeholder="输入操作者、动作或追踪号",
        )

    @router.get("/console/links/{kind}/{identifier}/{relation}", response_class=HTMLResponse)
    async def links_page(
        request: Request, kind: str, identifier: str, relation: str, after: str = "", q: str = ""
    ):
        if (kind, relation) not in {
            ("employees", "roles"),
            ("roles", "permission-sets"),
            ("permission-sets", "permissions"),
        }:
            raise DomainError(404, "not_found", "关联类型不存在")
        assoc = await operate(
            request,
            "access.links.get",
            {"kind": kind, "identifier": identifier, "relation": relation},
        )
        listing = await operate(
            request,
            "access.list",
            {"kind": relation, **table_query(request, {"after", "limit", "page", "q"})},
        )
        detail = await operate(request, "access.detail", {"kind": kind, "identifier": identifier})
        if relation == "permissions":
            listing["items"] = [
                describe_permission(row["code"], row["description"]) for row in listing["items"]
            ]
        field = "code" if relation == "permissions" else "id"
        visible = {item[field] for item in listing["items"]}
        names = {item["id"]: item["name"] for item in detail["links"]}
        return await render(
            request,
            "links.html",
            kind=kind,
            identifier=identifier,
            relation=relation,
            assoc=assoc,
            candidates=listing["items"],
            listing=listing,
            detail=detail,
            search=q,
            preserved_items=[
                {"id": key, "name": names.get(key, key)}
                for key in assoc["ids"]
                if key not in visible
            ],
        )

    @router.post("/console/links/{kind}/{identifier}/{relation}")
    async def links_replace(request: Request, kind: str, identifier: str, relation: str):
        form = dict(await request.form())
        require_csrf(request, form)
        ids = [v for k, v in sorted(form.items()) if k.startswith("id_")]
        field = "permissions" if kind == "permission-sets" else "ids"
        await operate(
            request,
            "access.links.replace",
            {
                "kind": kind,
                "identifier": identifier,
                "relation": relation,
                "body": {"version": form_version(form), field: ids},
            },
        )
        notify(
            request, "授权配置已保存，后续操作按最新配置校验。可在详情页检查配置权限和使用对象。"
        )
        return RedirectResponse(
            f"/console/links/{kind}/{quote(identifier)}/{relation}", status_code=303
        )

    def provision_notice(result):
        role = result.get("assigned_role")
        if role:
            return f"已开通并关联「{role['name']}」，按该角色当前配置权限生效。可在人员列表补充或撤回角色。"
        return "身份已绑定，未授予业务角色。需要时可在人员列表授权。"

    @router.post("/console/employees/new")
    async def employee_new(request: Request):
        form = dict(await request.form())
        require_csrf(request, form)
        result = await operate(
            request,
            "access.create",
            {
                "kind": "employees",
                "body": {
                    "name": str(form.get("name", "")),
                    "issuer": settings.issuer,
                    "subject": str(form.get("subject", "")),
                    "assign_default_role": form.get("assign_default_role") == "on",
                },
            },
        )
        notify(request, provision_notice(result))
        return RedirectResponse("/console/employees", status_code=303)

    @router.post("/console/roles/new")
    async def role_new(request: Request):
        form = dict(await request.form())
        require_csrf(request, form)
        result = await operate(
            request, "access.create", {"kind": "roles", "body": {"name": str(form.get("name", ""))}}
        )
        notify(request, "角色已创建，下一步关联已有权限集；创建不会自动授予任何员工。")
        return RedirectResponse(
            f"/console/links/roles/{quote(result['id'])}/permission-sets", status_code=303
        )

    @router.post("/console/permission-sets/new")
    async def permission_set_new(request: Request):
        form = dict(await request.form())
        require_csrf(request, form)
        result = await operate(
            request,
            "access.create",
            {"kind": "permission-sets", "body": {"name": str(form.get("name", ""))}},
        )
        notify(request, "权限集已创建，下一步选择具体权限；之后还需将权限集关联角色。")
        return RedirectResponse(
            f"/console/links/permission-sets/{quote(result['id'])}/permissions", status_code=303
        )

    @router.post("/console/employees/{identifier}/update")
    async def employee_update(request: Request, identifier: str):
        form = dict(await request.form())
        require_csrf(request, form)
        await operate(
            request,
            "access.employee.update",
            {
                "identifier": identifier,
                "body": {
                    "version": form_version(form),
                    "name": str(form.get("name", "")),
                    "active": form.get("active") == "on",
                },
            },
        )
        notify(
            request, "人员信息已更新；停用人员将无法继续访问业务数据，重新启用后仍沿用原授权配置。"
        )
        return RedirectResponse("/console/employees", status_code=303)

    @router.post("/console/employees/claim")
    async def employee_claim(request: Request):
        form = dict(await request.form())
        require_csrf(request, form)
        result = await operate(
            request,
            "access.identity.claim",
            {
                "pending_id": str(form.get("pending_id", "")),
                "name": str(form.get("name", "")),
                "assign_default_role": form.get("assign_default_role") == "on",
            },
        )
        notify(request, provision_notice(result))
        return RedirectResponse("/console/employees", status_code=303)

    @router.post("/auth/logout")
    async def logout(request: Request):
        require_csrf(request, dict(await request.form()))
        # 先撤销当前业务授权并清理对应身份会话，避免刷新时被 SSO 自动登录。
        current = await operate(request, "session.register", {})
        result = await operate(
            request,
            "session.change",
            {
                "identifier": current["id"],
                "version": current["version"],
                "action": "disable",
                "reason": "用户主动退出控制台",
            },
        )
        store.destroy(request.cookies.get(COOKIE))
        response = RedirectResponse(
            "/console/signed-out?cleanup=" + ("done" if result["cleanup"] == "done" else "failed"),
            status_code=303,
        )
        response.delete_cookie(COOKIE, path="/")
        return response

    @router.get("/console/signed-out", response_class=HTMLResponse)
    async def signed_out(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "signed_out.html",
            {
                "trace_id": request.state.trace_id,
                "logout_cleanup": request.query_params.get("cleanup")
                if request.query_params.get("cleanup") in {"done", "failed"}
                else None,
            },
        )

    register_assistant_ui(router, operate, render, require_csrf)
    register_assistant(router, operate, render, require_csrf, form_version, notify)
    register_client_sessions(router, operate, render, require_csrf, form_version, notify, store)
    register_accounts(
        router,
        application,
        store,
        operate,
        render,
        require_csrf,
        form_version,
        notify,
        principal_of,
        login_gate,
    )
    return router
