"""服务公开元数据；运行装配与离线契约使用同一份定义。"""

from importlib.metadata import version

from .product import PRODUCT

SERVICE_TITLE = PRODUCT["name"]
SERVICE_DESCRIPTION = PRODUCT["description"]
SERVICE_VERSION = version("office-data-service")
