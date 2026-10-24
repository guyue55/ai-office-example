"""唯一v2解码入口；普通SDK与显式强类型调用共用登录、连接池和码表。"""

import json
from importlib.resources import files

from office_contracts import CodeRegistry, ProtocolError, Success
from office_contracts.integrations.httpx import request_json

from .auth import ClientError


def application_registry() -> CodeRegistry:
    snapshot = json.loads(
        files("office_client").joinpath("business_codes.json").read_text(encoding="utf-8")
    )
    return CodeRegistry.from_export(snapshot)


class ContractClient:
    def __init__(self, client, *, registry: CodeRegistry | None = None):
        self.client = client
        self.registry = registry or application_registry()

    def request[T](self, method: str, path: str, data_model: type[T], **kwargs) -> Success[T]:
        if not path.startswith("/api/v2/") or ".." in path or "://" in path:
            raise ClientError("只能访问本服务的v2契约路径")
        if "headers" in kwargs or "auth" in kwargs or "follow_redirects" in kwargs:
            raise ClientError("认证与重定向策略由客户端统一管理")
        token = self.client.oauth.access_token()
        result = request_json(
            self.client.http,
            method,
            path,
            data_model,
            registry=self.registry,
            headers={"Authorization": "Bearer " + token},
            **kwargs,
        )
        if not isinstance(result, Success):
            raise ProtocolError("此接口声明JSON结果，不接受原生空响应")
        return result
