"""匿名连接核验；不读取、刷新或转发员工凭证，不写业务数据。"""

import httpx

from .auth import ClientError, KeyringStore, OAuth, trusted_url


class ConnectionCheckError(ClientError):
    """匿名诊断的有限错误信息；不携带响应正文、凭证或异常repr。"""

    def __init__(self, message, *, code, stage, status=None):
        super().__init__(message)
        self.code, self.stage, self.status = code, stage, status

    def result(self, service):
        return {
            "ok": False,
            "service": service,
            "online_verified": False,
            "identity_verified": False,
            "credential_io": False,
            "issue": {
                "code": self.code,
                "stage": self.stage,
                "http_status": self.status,
                "message": str(self),
            },
        }


def check_connection(service: str, *, allow_lan_http_demo=False) -> dict:
    try:
        service = trusted_url(service, allow_lan_http_demo=allow_lan_http_demo)
    except (ClientError, ValueError):
        raise ConnectionCheckError(
            "服务地址格式或传输模式无效，请先通过AI核对企业配置",
            code="invalid_address",
            stage="configuration",
        ) from None
    # KeyringStore构造仅登记服务命名空间；config不调用store的任何方法。
    oauth = OAuth(service, KeyringStore(service), allow_lan_http_demo=allow_lan_http_demo)
    stage = "service_ready"
    try:
        oauth.http.timeout = httpx.Timeout(5)
        response = oauth.http.get(service + "/health/ready")
        response.raise_for_status()
        if response.json() != {"status": "ok"}:
            raise ConnectionCheckError(
                "服务就绪响应不匹配，请核对地址", code="invalid_response", stage=stage
            )
        stage = "authorization_config"
        config = oauth.config()
        stage = "identity_discovery"
        response = oauth.http.get(config["issuer"] + "/.well-known/openid-configuration")
        response.raise_for_status()
        discovery = response.json()
        if not isinstance(discovery, dict) or discovery.get("issuer") != config["issuer"]:
            raise ConnectionCheckError(
                "身份服务公开地址不一致，请联系管理员", code="issuer_mismatch", stage=stage
            )
        return {
            "ok": True,
            "service": service,
            "issuer": config["issuer"],
            "online_verified": True,
            "identity_verified": False,
            "credential_io": False,
            "notice": "服务与身份入口可达；员工身份及权限仍须登录后通过whoami核验",
        }
    except ConnectionCheckError:
        raise
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        code = (
            "unexpected_redirect"
            if 300 <= status < 400
            else "service_not_ready"
            if status == 503
            else "http_error"
        )
        raise ConnectionCheckError(
            f"连接检查失败（HTTP {status}），请核对地址及服务状态",
            code=code,
            stage=stage,
            status=status,
        ) from None
    except httpx.InvalidURL:
        raise ConnectionCheckError(
            "服务地址格式无效，请核对主机及端口", code="invalid_address", stage=stage
        ) from None
    except httpx.RequestError:
        raise ConnectionCheckError(
            "无法连接，请核对地址、网络及证书；不会自动切换地址或关闭校验",
            code="transport_error",
            stage=stage,
        ) from None
    except (ValueError, ClientError):
        raise ConnectionCheckError(
            "连接配置或响应不符合要求，请核对企业地址及身份入口",
            code="invalid_response",
            stage=stage,
        ) from None
    finally:
        oauth.http.close()
