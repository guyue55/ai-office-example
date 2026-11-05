"""外部身份开通入口：页面适配复用授权预览和执行，不改变登录准入。"""

import json

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.common.errors import DomainError
from app.entrypoints.web.tables import table_query
from app.modules.access.schemas import PendingList


def register_identity_access(router, settings, operate, render, require_csrf, notify):
    async def require_manager(request):
        identity = await operate(request, "me", {})
        if "access.manage" not in identity["permissions"]:
            raise DomainError(403, "permission_denied", "需要人员管理权限")

    @router.get("/console/employees/pending", response_class=HTMLResponse)
    async def pending(request: Request):
        listing = await operate(
            request, "access.identity.pending", table_query(request, PendingList.model_fields)
        )
        return await render(
            request,
            "pending_identities.html",
            listing=listing,
            items=listing["items"],
            search=request.query_params.get("q", ""),
        )

    @router.get("/console/employees/external", response_class=HTMLResponse)
    async def external(request: Request):
        await require_manager(request)
        return await render(request, "external_identity.html", issuer=settings.issuer)

    async def preview(request, change):
        result = await operate(request, "access.change.preview", {"change": change})
        return await render(
            request,
            "identity_preview.html",
            preview=result,
            change_json=json.dumps(result["change"], ensure_ascii=False),
        )

    @router.post("/console/employees/new", response_class=HTMLResponse)
    async def prepare_external(request: Request):
        form = dict(await request.form())
        require_csrf(request, form)
        return await preview(
            request,
            {
                "operation": "access.create",
                "payload": {
                    "kind": "employees",
                    "body": {
                        "name": str(form.get("name", "")),
                        "issuer": settings.issuer,
                        "subject": str(form.get("subject", "")),
                        "assign_default_role": form.get("assign_default_role") == "on",
                    },
                },
            },
        )

    @router.post("/console/employees/claim", response_class=HTMLResponse)
    async def prepare_pending(request: Request):
        form = dict(await request.form())
        require_csrf(request, form)
        return await preview(
            request,
            {
                "operation": "access.identity.claim",
                "payload": {
                    "pending_id": str(form.get("pending_id", "")),
                    "name": str(form.get("name", "")),
                    "assign_default_role": form.get("assign_default_role") == "on",
                },
            },
        )

    @router.post("/console/employees/identity-confirm")
    async def confirm(request: Request):
        form = dict(await request.form())
        require_csrf(request, form)
        try:
            change = json.loads(str(form.get("change_json", "")))
        except (ValueError, TypeError):
            raise DomainError(422, "invalid_input", "开通资料无效，请重新核对") from None
        # 此确认入口仅接收两种开通操作，不能借隐藏字段转为其他授权修改。
        if not isinstance(change, dict) or not (
            change.get("operation") == "access.identity.claim"
            or (
                change.get("operation") == "access.create"
                and isinstance(change.get("payload"), dict)
                and change["payload"].get("kind") == "employees"
            )
        ):
            raise DomainError(422, "invalid_input", "请从外部身份开通入口重新预览")
        result = await operate(
            request,
            "access.change.apply",
            {
                "change": change,
                "confirmation_digest": str(form.get("digest", "")),
            },
        )
        role = result.get("assigned_role")
        notify(
            request,
            f"已开通并关联「{role['name']}」，凭证仍由外部身份系统维护。"
            if role
            else "身份已绑定，未授予业务角色；凭证仍由外部身份系统维护。",
        )
        return RedirectResponse("/console/employees", status_code=303)
