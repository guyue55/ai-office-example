"""HTTP/MCP共享的协议错误适配；业务错误类型不反向依赖此层。"""

from functools import lru_cache
from uuid import uuid4

from office_contracts import Meta, Recovery
from office_contracts.integrations.http_boundary import Renderer

from app.application.codebook import build_registry, legacy_code
from app.common.errors import DomainError, log_rejection


def domain_response(error: DomainError, trace_id: str, *, registry):
    code = legacy_code(error.code, error.status)
    spec = registry.by_code(code)
    status = error.status if spec and error.status in spec.statuses else 500
    meta = Meta(
        trace_id=trace_id,
        recovery=Recovery(
            action=(
                "query_operation"
                if status >= 500
                else "reauthenticate"
                if status == 401
                else "check_permissions"
                if status == 403
                else "refresh"
                if status == 409
                else "correct_request"
            ),
            write_outcome="unknown" if status >= 500 else "not_applied",
        ),
    )
    return Renderer(registry).response(code, status, meta=meta)


@lru_cache(maxsize=1)
def _error_registry():
    """错误响应与业务码表共用一份注册表；进程内构建一次，请求路径无文件I/O。"""
    return build_registry()


def error_response(error: DomainError, trace_id):
    """v2 单轨失败响应：符号经码表映射为数字码并携带统一 meta，禁止回退旧字符串格式。"""
    # 原始ASGI或MCP上下文可能没有追踪号；失败响应必须可追查，缺失时即时生成。
    return domain_response(error, trace_id or str(uuid4()), registry=_error_registry())


def tool_error_text(error: DomainError, trace_id=None) -> str:
    """MCP保留原生isError，业务正文复用HTTP数字码和安全恢复信息。"""
    return error_response(error, trace_id).body.decode("utf-8")


async def protocol_http_error(request, error):
    codes = {
        400: "invalid_request",
        404: "not_found",
        405: "method_not_allowed",
        415: "unsupported_media_type",
    }
    domain = DomainError(
        error.status_code,
        codes.get(error.status_code, "http_error"),
        {
            400: "请求格式无效",
            404: "资源不存在",
            405: "请求方法不受支持",
            415: "请求内容类型不受支持",
        }.get(error.status_code, "请求未成功"),
    )
    response = error_response(domain, request.scope.get("state", {}).get("trace_id"))
    if error.headers and "Allow" in error.headers:
        response.headers["Allow"] = error.headers["Allow"]
    return response


class DomainErrorMiddleware:
    """覆盖协议认证中间件抛出的服务故障，不回显内部异常或凭证。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except DomainError as error:
            if scope["type"] != "http" or started:
                raise
            log_rejection(scope.get("state", {}).get("trace_id"), error.code, error.status, "mcp")
            await error_response(error, scope.get("state", {}).get("trace_id"))(
                scope, receive, send
            )
