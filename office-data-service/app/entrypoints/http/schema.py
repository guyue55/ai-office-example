"""公开契约构建：只组合真实路由，不初始化数据库、身份客户端或秘密文件。"""

from fastapi import FastAPI

from app.application.codebook import build_registry
from app.common.metadata import SERVICE_DESCRIPTION, SERVICE_TITLE, SERVICE_VERSION
from app.entrypoints.http.public import public_router
from app.entrypoints.http.routes import router


def public_schema():
    app = FastAPI(title=SERVICE_TITLE, description=SERVICE_DESCRIPTION, version=SERVICE_VERSION)
    # 仅生成描述，不作为可运行服务；None不会进入请求处理。
    app.include_router(public_router(None))
    app.include_router(router(None, registry=build_registry(), prefix="/api/v2"))
    return app.openapi()
