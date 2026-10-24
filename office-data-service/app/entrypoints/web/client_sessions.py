"""客户端授权管理页面；共用应用契约、CSRF、分页和确认弹窗。"""

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.entrypoints.web.responses import COOKIE, TEMPLATES
from app.entrypoints.web.tables import table_query
from app.modules.access.session_schemas import SessionList


def register_client_sessions(router, operate, render, csrf, version, notify, store):
    @router.get("/console/client-authorizations")
    async def listing(request: Request):
        filters = table_query(request, SessionList.model_fields)
        result = await operate(request, "session.list", filters)
        return await render(request, "client_authorizations.html", listing=result)

    @router.post("/console/client-authorizations/{identifier}/change")
    async def change(request: Request, identifier: str):
        form = dict(await request.form())
        csrf(request, form)
        result = await operate(
            request,
            "session.change",
            {
                "identifier": identifier,
                "version": version(form),
                "action": str(form.get("action", "")),
                "reason": str(form.get("reason", "")),
            },
        )
        if result["current"]:
            # 不再用刚撤销的身份渲染me或自动SSO重登，清理失败也不会循环回跳。
            store.destroy(request.cookies.get(COOKIE))
            response = TEMPLATES.TemplateResponse(
                request,
                "signed_out.html",
                {
                    "trace_id": request.state.trace_id,
                    "authorization_ended": True,
                    "cleanup": result["cleanup"],
                },
            )
            response.delete_cookie(COOKIE, path="/")
            return response
        notify(
            request,
            "业务访问已停止。"
            + (
                "身份登录已结束。"
                if result["cleanup"] == "done"
                else "身份会话清理待完成，可在列表重试；业务访问已被阻止。"
            ),
        )
        return RedirectResponse(
            "/console/client-authorizations?scope="
            + ("own" if result["employee_id"] == (await operate(request, "me"))["id"] else "all"),
            status_code=303,
        )
