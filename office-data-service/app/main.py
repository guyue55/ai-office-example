import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, Request
from office_contracts.integrations.fastapi import install_fastapi
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.application.codebook import build_registry
from app.application.service import Application
from app.common.errors import DomainError, log_rejection
from app.common.metadata import SERVICE_DESCRIPTION, SERVICE_TITLE, SERVICE_VERSION
from app.common.tracing import trace_context
from app.entrypoints.errors import domain_response
from app.entrypoints.http.limits import BodyLimitMiddleware
from app.entrypoints.http.public import public_router
from app.entrypoints.http.routes import router
from app.entrypoints.mcp.routing import ProtocolMount
from app.entrypoints.mcp.server import build_mcp
from app.entrypoints.web.console import web_router
from app.entrypoints.web.responses import is_web_path, web_error_response
from app.infrastructure.config import Settings
from app.infrastructure.database import async_sessions
from app.modules.access.authentication import TokenVerifier


def create_app(settings: Settings | None = None):
    settings = settings or Settings.load()
    if settings.database_user != "office_app":
        raise RuntimeError("运行服务必须使用最小权限 office_app，禁止使用迁移或管理员账号")
    factory = async_sessions(settings)
    application = Application(settings, factory, TokenVerifier(settings))
    server, mcp_app = build_mcp(application)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await application.account_service().runner.start()
            if settings.agent_config_file is not None:
                await application.agent_service().start()
            async with server.session_manager.run():
                yield
        finally:
            # MCP 启动失败、请求异常或取消退出也必须释放连接池。
            try:
                try:
                    if application._agent is not None:
                        await application._agent.close()
                finally:
                    await application.account_service().runner.close()
            finally:
                try:
                    await application.verifier.aclose()
                finally:
                    await factory.kw["bind"].dispose()

    app = FastAPI(
        title=SERVICE_TITLE,
        description=SERVICE_DESCRIPTION,
        version=SERVICE_VERSION,
        lifespan=lifespan,
    )
    app.state.application = application
    registry = build_registry()
    app.state.contract_registry = registry
    # 只信任实例公开地址的主机名与本机探针，不开放通配Host。
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            host
            for host in (
                urlparse(settings.public_url).hostname,
                "127.0.0.1",
                "localhost",
                "testserver",
            )
            if host
        ],
    )

    app.add_middleware(BodyLimitMiddleware, timeout=settings.request_body_timeout)

    @app.middleware("http")
    async def trace(request, call_next):
        request.state.trace_id = request.scope.get("state", {}).get("trace_id") or str(uuid4())
        trace_token = trace_context.set(request.state.trace_id)
        try:
            try:
                response = await call_next(request)
            except Exception as error:  # noqa: BLE001 — 最外层HTTP安全响应边界
                # 仅记录异常类型和关联号，禁止把请求资料、SQL参数或凭证写入日志。
                logging.getLogger("office.service").error(
                    "[Trace: OfficeService/HTTP/failure] trace_id=%s error_type=%s",
                    request.state.trace_id,
                    type(error).__name__,
                )
                failure = DomainError(500, "internal_error", "服务内部错误")
                response = (
                    web_error_response(request, failure)
                    if is_web_path(request.url.path)
                    else domain_response(failure, request.state.trace_id, registry=registry)
                )
            if response.status_code >= 400 and response.headers.get("content-type", "").startswith(
                "text/plain"
            ):
                response = domain_response(
                    DomainError(
                        response.status_code,
                        "invalid_request" if response.status_code == 400 else "internal_error",
                        "请求未成功",
                    ),
                    request.state.trace_id,
                    registry=registry,
                )
            logging.getLogger("office.service").info(
                "[Trace: OfficeService/HTTP/end] trace_id=%s method=%s status=%s",
                request.state.trace_id,
                request.method,
                response.status_code,
            )
            response.headers["X-Trace-ID"] = request.state.trace_id
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
            )
            return response
        finally:
            trace_context.reset(trace_token)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        log_rejection(request.state.trace_id, error.code, error.status, "http")
        if is_web_path(request.url.path):
            return web_error_response(request, error)
        return domain_response(error, request.state.trace_id, registry=registry)

    # 健康检查、未知路径仍用 v2 失败体；页面路径返回 HTML 并保留 HTTP 状态。
    # /api/v2 内部由 install_fastapi 接管，其他路径经 previous 委托到这里。
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        codes = {
            400: "invalid_request",
            404: "not_found",
            405: "method_not_allowed",
            415: "unsupported_media_type",
        }
        domain = DomainError(
            error.status_code,
            codes.get(error.status_code, "http_error"),
            "请求未成功",
        )
        log_rejection(request.state.trace_id, domain.code, domain.status, "http")
        response = (
            web_error_response(request, domain)
            if is_web_path(request.url.path)
            else domain_response(domain, request.state.trace_id, registry=registry)
        )
        if error.headers and "Allow" in error.headers:
            response.headers["Allow"] = error.headers["Allow"]
        return response

    app.include_router(public_router(application))

    app.include_router(router(application, registry=registry, prefix="/api/v2"))
    install_fastapi(app, registry=registry, api_prefixes=("/api/v2",))
    # Web 控制台（BFF）：仅在配置了会话密钥时启用；与 API 同一业务入口与权限。
    if settings.web_session_secret_file is not None:
        app.include_router(web_router(application))
    # 主应用持有 lifespan；完整挂载以保留官方认证与上下文中间件。
    app.router.routes.append(ProtocolMount("/", app=mcp_app))
    return app
