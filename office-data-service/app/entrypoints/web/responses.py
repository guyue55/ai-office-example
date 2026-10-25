"""页面响应边界；错误页不再次查询身份或业务，避免异常递归。"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.application.codebook import legacy_code
from app.common.errors import DomainError
from app.common.product import PRODUCT
from app.modules.access.session_errors import SessionEnded

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["product"] = PRODUCT
TEMPLATES.env.trim_blocks = True
TEMPLATES.env.lstrip_blocks = True
TEMPLATES.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "ensure_ascii": False}


def business_time(value: datetime) -> str:
    """存储保持带时区时间；页面统一显示北京时间，保留跨年辨识信息。"""
    return value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")


TEMPLATES.env.filters["business_time"] = business_time

COOKIE = "office_web_session"
LOGIN_COOKIE = "office_web_login"


def recovery_path(path: str) -> str | None:
    """仅从本站路由生成恢复入口，不信任 Referer 或客户端传入的跳转地址。"""
    if path.startswith("/console/links/"):
        return path
    for kind in ("employees", "roles", "permission-sets"):
        if path == f"/console/{kind}/new" or (
            kind == "employees" and path.startswith("/console/employees/")
        ):
            return f"/console/{kind}"
    return None


def is_web_path(path: str) -> bool:
    return (
        path == "/console"
        or path.startswith("/console/")
        or path in {"/auth/callback", "/auth/logout"}
    )


def web_error_response(request: Request, error: DomainError):
    if isinstance(error, SessionEnded):
        ended_response = TEMPLATES.TemplateResponse(
            request,
            "signed_out.html",
            {"trace_id": request.state.trace_id, "authorization_ended": True},
            status_code=401,
        )
        ended_response.delete_cookie(COOKIE, path="/")
        return ended_response
    if error.status == 401:
        response = RedirectResponse("/console", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response
    return TEMPLATES.TemplateResponse(
        request,
        "error.html",
        {
            "message": error.message,
            "code": legacy_code(error.code, error.status),
            "trace_id": request.state.trace_id,
            "recovery_path": recovery_path(request.url.path),
            "conflict": error.status == 409,
        },
        status_code=error.status,
    )
