"""健康与授权发现入口；导出Schema时只登记路由，不创建运行依赖。"""

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.common.errors import DomainError


def public_router(application):
    routes = APIRouter()

    @routes.get("/health/live")
    async def live():
        return {"status": "ok"}

    @routes.get("/health/ready")
    async def ready():
        try:
            async with application.sessions() as session:
                revision = await session.scalar(
                    text("SELECT revision FROM access_revision WHERE id=1")
                )
                if revision is None:
                    raise DomainError(503, "not_initialized", "基础权限尚未初始化")
        except (DBAPIError, PoolTimeoutError):
            raise DomainError(503, "database_unavailable", "数据服务尚未就绪") from None
        return {"status": "ok"}

    @routes.get("/auth/config")
    async def auth_config():
        return {
            "issuer": application.settings.issuer,
            "client_id": application.settings.client_id,
            "redirect_uri": application.settings.redirect_uri,
            "scope": "openid office",
            "resource": application.settings.audience,
        }

    return routes
