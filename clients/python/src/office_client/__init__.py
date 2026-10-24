"""企业办公客户端：只通过受控 HTTP 访问服务，不包含数据库驱动。"""

from .client import OfficeClient, ServiceError

__all__ = ["OfficeClient", "ServiceError"]
