"""统一账号页面；复用会话、CSRF、应用操作和模板，不直接访问数据库。"""

import base64
import hashlib
import json
import time
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from app.common.errors import DomainError, log_rejection, require, validation_error
from app.entrypoints.web.responses import COOKIE, LOGIN_COOKIE
from app.entrypoints.web.session import new_csrf
from app.entrypoints.web.tables import table_query
from app.modules.access.account_schemas import AccountHistory


def register_accounts(
    router, application, store, operate, render, csrf, version, notify, principal_of, login_gate
):
    settings = application.settings

    async def permission(request, *codes):
        me = await operate(request, "me")
        require(
            all(code in me["permissions"] for code in codes),
            403,
            "permission_denied",
            "当前职责没有此操作权限",
        )
        return me

    async def preview(request, command, key=None, target=None):
        result = await operate(request, "account.preview", {"request": command})
        if command.get("identifier"):
            target = await operate(request, "account.detail", {"identifier": command["identifier"]})
            require(
                target["version"] == command["version"],
                409,
                "version_conflict",
                "资料已更新，请返回重新核对",
            )
            if command["kind"] == "update":
                require(
                    target.get("account") is not None,
                    409,
                    "constraint_conflict",
                    "原登录资料暂时无法核实，请刷新资料后重新核对",
                )
        return await render(
            request,
            "account_preview.html",
            preview=result,
            target=target,
            request_json=json.dumps(result["request"], ensure_ascii=False),
            key=key or uuid4().hex,
        )

    @router.get("/console/accounts/new")
    async def new_employee(request: Request):
        await permission(request, "identity.create", "access.manage", "identity.read")
        capability = await operate(request, "account.capabilities")
        require(capability["enabled"], 503, "identity_unavailable", "账号管理尚未配置")
        return await render(request, "account_new.html", capability=capability, key=uuid4().hex)

    @router.post("/console/accounts/new")
    async def create_preview(request: Request):
        form = dict(await request.form())
        csrf(request, form)
        try:
            return await preview(
                request,
                {
                    "kind": "create",
                    "name": str(form.get("name", "")),
                    "username": str(form.get("username", "")),
                    "email": str(form.get("email", "")),
                    "role_ids": None if form.get("default_role") == "on" else [],
                    "mode": str(form.get("mode", "temporary")),
                },
                str(form.get("key", "")),
            )
        except (DomainError, ValidationError) as caught:
            error = validation_error(caught) if isinstance(caught, ValidationError) else caught
            if error.status not in {409, 422}:
                raise
            # 仅恢复未执行写入的开户预览；重新核对职责，不把权限拒绝当作格式错误。
            await permission(request, "identity.create", "access.manage", "identity.read")
            capability = await operate(request, "account.capabilities")
            require(capability["enabled"], 503, "identity_unavailable", "账号管理尚未配置")
            values: dict[str, str | bool] = {
                key: str(form.get(key, ""))[:limit]
                for key, limit in (("name", 100), ("username", 64), ("email", 254), ("mode", 20))
            }
            values["default_role"] = form.get("default_role") == "on"
            response = await render(
                request,
                "account_new.html",
                capability=capability,
                key=str(form.get("key", ""))[:128] or uuid4().hex,
                form_values=values,
                form_error=error.message,
            )
            response.status_code = error.status
            response.headers["Cache-Control"] = "no-store"
            log_rejection(request.state.trace_id, error.code, error.status, "web")
            return response

    @router.get("/console/accounts/link")
    async def link_accounts(request: Request, q: str = ""):
        await permission(request, "access.manage", "identity.read")
        found = await operate(request, "account.search", {"q": q}) if q else None
        return await render(request, "account_link.html", found=found, search=q)

    @router.post("/console/accounts/link")
    async def link_preview(request: Request):
        form = dict(await request.form())
        csrf(request, form)
        # 显示身份从服务端搜索结果取得；隐藏字段只作查询线索，不作身份事实。
        lookup = str(form.get("lookup", ""))
        found = await operate(request, "account.search", {"q": lookup})
        target = next(
            (item for item in found["items"] if item["id"] == str(form.get("remote_id", ""))),
            None,
        )
        if target is None:
            raise DomainError(409, "constraint_conflict", "目标账号无法核实，请重新搜索并关联")
        require(
            not target.get("linked_employee_id"),
            409,
            "constraint_conflict",
            "账号已关联人员，请刷新核对",
        )
        return await preview(
            request,
            {
                "kind": "link",
                "remote_id": str(form.get("remote_id", "")),
                "name": str(form.get("name", "")),
                "role_ids": None if form.get("default_role") == "on" else [],
            },
            target=target,
        )

    @router.get("/console/accounts/employees/{identifier}")
    async def employee_account(request: Request, identifier: str):
        detail = await operate(request, "account.detail", {"identifier": identifier})
        history = await operate(request, "account.history", {"identifier": identifier, "limit": 20})
        capability = await operate(request, "account.capabilities")
        return await render(
            request, "account_detail.html", detail=detail, history=history, capability=capability
        )

    @router.post("/console/accounts/employees/{identifier}/preview")
    async def action_preview(request: Request, identifier: str):
        form = dict(await request.form())
        csrf(request, form)
        kind = str(form.get("kind", ""))
        command = {"kind": kind, "identifier": identifier, "version": version(form)}
        if kind == "update":
            command.update(name=str(form.get("name", "")), email=str(form.get("email", "")))
        if kind == "reset":
            command["mode"] = str(form.get("mode", "temporary"))
        return await preview(request, command)

    @router.post("/console/accounts/submit")
    async def submit(request: Request):
        form = dict(await request.form())
        csrf(request, form)
        try:
            command = json.loads(str(form.get("request_json", "")))
        except ValueError:
            raise DomainError(422, "invalid_input", "操作内容无效，请重新预览") from None
        result = await operate(
            request,
            "account.submit",
            {
                "request": command,
                "confirmation_digest": str(form.get("digest", "")),
                "idempotency_key": str(form.get("key", "")),
            },
        )
        notify(request, "操作已受理，请在此查看进度；不要重复开户。")
        return RedirectResponse(result["next_url"], status_code=303)

    @router.get("/console/accounts/operations")
    async def operations(
        request: Request, after: str = "", identifier: str | None = None, limit: int = 20
    ):
        listing = await operate(
            request, "account.history", table_query(request, AccountHistory.model_fields)
        )
        return await render(request, "account_operations.html", listing=listing)

    @router.get("/console/accounts/operations/{identifier}")
    async def operation(request: Request, identifier: str):
        result = await operate(request, "account.operation", {"identifier": identifier})
        session = store.load(request.cookies.get(COOKIE))
        recent = (
            session.get("credential_operation") == identifier
            and time.time() - session.get("recent_auth", 0) < 300
        )
        return await render(request, "account_operation.html", operation=result, recent=recent)

    @router.post("/console/accounts/operations/{identifier}/resume-preview")
    async def resume_preview(request: Request, identifier: str):
        form = dict(await request.form())
        csrf(request, form)
        result = await operate(
            request,
            "account.resume_preview",
            {"identifier": identifier, "mode": form.get("mode") or None},
        )
        return await render(
            request,
            "account_preview.html",
            preview=result,
            request_json="",
            key="",
            resume=identifier,
            operation_version=version(form),
        )

    @router.post("/console/accounts/operations/{identifier}/resume")
    async def resume(request: Request, identifier: str):
        form = dict(await request.form())
        csrf(request, form)
        result = await operate(
            request,
            "account.resume",
            {
                "identifier": identifier,
                "version": version(form),
                "confirmation_digest": str(form.get("digest", "")),
                "mode": form.get("mode") or None,
            },
        )
        notify(request, "已重新确认，继续原操作。")
        return RedirectResponse(result["next_url"], status_code=303)

    @router.post("/console/accounts/operations/{identifier}/credential")
    async def credential(request: Request, identifier: str):
        form = dict(await request.form())
        csrf(request, form)
        session = store.load(request.cookies.get(COOKIE))
        require(
            session.get("credential_operation") == identifier
            and time.time() - session.get("recent_auth", 0) < 300,
            403,
            "permission_denied",
            "准备凭证前请重新验证本人身份",
        )
        principal = await principal_of(request)
        # 明文仅存在此响应内，不进入Application公共契约、模型工具或持久化。
        password = await application.account_service().runner.credential(
            identifier, principal, request.state.trace_id, version(form)
        )
        session.pop("credential_operation", None)
        response = await render(
            request, "account_credential.html", password=password, identifier=identifier
        )
        response.headers.update(
            {
                "Cache-Control": "no-store, private",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
                "X-Robots-Tag": "noindex, noarchive",
            }
        )
        return response

    async def begin_security(request, purpose, operation=None):
        me = await operate(request, "me")
        principal = await principal_of(request)
        if purpose == "credential":
            await permission(request, "identity.credentials.manage", "identity.read")
            await operate(request, "account.operation", {"identifier": operation})
        source = login_gate.admit_request(request, store)
        state, verifier, binding, nonce = (
            "st_" + new_csrf(),
            new_csrf() + new_csrf(),
            new_csrf(),
            new_csrf(),
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        store.put_pending(
            state,
            {
                "verifier": verifier,
                "csrf": new_csrf(),
                "binding": binding,
                "purpose": purpose,
                "_source": source,
                "subject": principal.subject,
                "operation": operation,
                "started": int(time.time()),
                "nonce": nonce,
            },
        )
        params = {
            "client_id": settings.web_client_id,
            "redirect_uri": settings.public_url + "/auth/callback",
            "response_type": "code",
            "scope": "openid office",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
            "prompt": "login",
            "max_age": "0",
            "login_hint": principal.claims.get("preferred_username", me["name"]),
        }
        if purpose == "password":
            params["kc_action"] = "UPDATE_PASSWORD"
        response = RedirectResponse(
            settings.issuer + "/protocol/openid-connect/auth?" + urlencode(params), status_code=303
        )
        response.set_cookie(
            LOGIN_COOKIE + "_" + state,
            binding,
            max_age=600,
            path="/",
            httponly=True,
            samesite="lax",
            secure=settings.public_url.startswith("https://"),
        )
        return response

    @router.post("/console/accounts/operations/{identifier}/verify")
    async def verify(request: Request, identifier: str):
        csrf(request, dict(await request.form()))
        return await begin_security(request, "credential", identifier)

    @router.get("/console/security")
    async def security(request: Request, returned: bool = False):
        return await render(request, "account_security.html", returned=returned)

    @router.post("/console/security/password")
    async def self_password(request: Request):
        csrf(request, dict(await request.form()))
        return await begin_security(request, "password")
