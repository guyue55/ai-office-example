"""FastAPI公共适配入口；安装范围显式指定，不改其他协议。"""

import re

from fastapi.exceptions import RequestValidationError, ResponseValidationError
from starlette.exceptions import HTTPException

from ..errors import ConfigurationError, ContractError, ProtocolError
from ..models import Issue
from .http_boundary import ContractBoundary, Renderer
from .http_router import ContractRouter

__all__ = ["ContractRouter", "install_fastapi"]


def install_fastapi(app, *, registry, api_prefixes=("/api/v2",), **boundary_options):
    if getattr(app.state, "contract_installed", False):
        raise ConfigurationError("契约边界不能重复安装")
    app.state.contract_installed = True
    renderer = Renderer(registry)
    previous = dict(app.exception_handlers)

    def matches(request):
        return any(
            request.url.path == p.rstrip("/") or request.url.path.startswith(p.rstrip("/") + "/")
            for p in api_prefixes
        )

    async def handler(request, error):
        if not matches(request):
            for kind in type(error).__mro__:
                original = previous.get(kind)
                if original:
                    return await original(request, error)
            raise error
        headers: dict[str, str] = {}
        details: tuple[Issue, ...] = ()
        if isinstance(error, ContractError):
            spec = registry.by_code(error.code)
            # planned 码视为未登记，回落 500001，防止未实现能力被提前出站承诺。
            code, status = (
                (spec.code, min(spec.statuses))
                if spec and spec.severity == "error" and spec.availability == "current-source"
                else (500001, 500)
            )
        elif isinstance(error, RequestValidationError):
            code, status = 422001, 422
            issues = error.errors()
            details = tuple(_issue_from_loc(entry) for entry in issues[:20])
        elif isinstance(error, (ResponseValidationError, ProtocolError)):
            code, status = 500002, 500
        elif isinstance(error, HTTPException) and 400 <= error.status_code <= 599:
            status = error.status_code
            code = {
                400: 400001,
                401: 401001,
                403: 403001,
                404: 404001,
                405: 405001,
                408: 408001,
                413: 413001,
                415: 415001,
                422: 422001,
                500: 500001,
            }.get(status, 400099 if status < 500 else 500099)
            for key, value in (error.headers or {}).items():
                if (
                    key.lower() in {"allow", "www-authenticate", "retry-after", "location"}
                    and "\r" not in value
                    and "\n" not in value
                ):
                    headers[key] = value
        else:
            code, status = 500001, 500
        if status == 401:
            headers.setdefault("WWW-Authenticate", "Bearer")
        return renderer.response(
            code,
            status,
            headers=headers,
            details=details,
            truncated=isinstance(error, RequestValidationError) and len(error.errors()) > 20,
        )

    for kind in (
        ContractError,
        ProtocolError,
        RequestValidationError,
        ResponseValidationError,
        HTTPException,
    ):
        app.add_exception_handler(kind, handler)
    app.add_middleware(
        ContractBoundary, registry=registry, prefixes=api_prefixes, **boundary_options
    )

    seen = set()
    for path, operations in app.openapi().get("paths", {}).items():
        if not any(
            path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in api_prefixes
        ):
            continue
        for operation in operations.values():
            if isinstance(operation, dict) and "operationId" in operation:
                identifier = operation["operationId"]
                if identifier in seen:
                    raise ConfigurationError("受管operation_id重复")
                seen.add(identifier)

    app.openapi_schema = None  # 校验不冻结Schema；以后显式添加的路由仍能进入文档。


def _issue_from_loc(entry):
    """把 Pydantic 校验错误的 loc 转为 Issue；仅使用字段路径，不回显请求值。"""
    pattern = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
    path = []
    for item in entry.get("loc", ()):
        if isinstance(item, bool):  # bool 是 int 子类，且不是合法字段路径片段
            continue
        if (
            isinstance(item, int)
            and item >= 0
            or isinstance(item, str)
            and item != "__root__"
            and pattern.fullmatch(item)
        ):
            path.append(item)
    if not path:
        path = ["field"]
    return Issue(path=tuple(path[:8]), reason="invalid")
