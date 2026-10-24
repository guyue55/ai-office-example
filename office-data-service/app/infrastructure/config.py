"""只从显式配置和秘密引用构建运行环境，禁止打印连接口令。"""

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.common.product import PRODUCT


class DatabaseRuntime(BaseModel):
    """单进程连接预算；多进程部署需按进程数计算总连接数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pool_size: int = Field(default=5, ge=1, le=100)
    max_overflow: int = Field(default=5, ge=0, le=100)
    pool_timeout: float = Field(default=3, gt=0, le=60)
    statement_timeout_ms: int = Field(default=10000, ge=100, le=300000)
    lock_timeout_ms: int = Field(default=5000, ge=100, le=60000)


class IdentityManagement(BaseModel):
    """可选账号管理连接；未配置时已有登录和业务权限继续工作。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    client_id: str = Field(min_length=1, max_length=100)
    client_secret_file: Path
    email_enabled: bool = False


class TokenIntrospection(BaseModel):
    """可选在线令牌检查；独立机密客户端，无需用户管理权限。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    client_id: str = Field(min_length=1, max_length=100)
    client_secret_file: Path


class LoginProtection(BaseModel):
    """来源配额为未完成登录事务，不是员工设备或有效登录数量。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pending_per_source: int = Field(default=128, ge=1, le=10000)
    burst: int = Field(default=60, ge=1, le=10000)
    refill_per_second: float = Field(default=2, gt=0, le=1000)
    max_sources: int = Field(default=2048, ge=1, le=10000)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    database_user: str
    database_password_file: Path
    database_host: str = "127.0.0.1"
    database_port: int = Field(default=55432, ge=1, le=65535)
    database_runtime: DatabaseRuntime = Field(default_factory=DatabaseRuntime)
    database_name: str = "office"
    identity_management: IdentityManagement | None = None
    token_introspection: TokenIntrospection | None = None
    allow_lan_http_demo: bool = Field(default=False, strict=True)
    issuer: str
    audience: str
    public_url: str
    request_body_timeout: float = Field(default=10, gt=0, le=120)
    operation_timeout: float = Field(default=15, gt=0, le=120)
    auth_workers: int = Field(default=8, ge=1, le=64)
    client_id: str = PRODUCT["client_id"]
    redirect_uri: str = "http://127.0.0.1:8765/callback"
    web_session_secret_file: Path | None = None
    web_client_id: str = Field(default=PRODUCT["web_client_id"], min_length=1, max_length=100)
    web_session_ttl: int = Field(default=7200, ge=300, le=86400)
    web_authentication_max_age: int = Field(default=36000, ge=300, le=2592000)
    web_session_limit: int = Field(default=2048, ge=1, le=10000)
    web_pending_limit: int = Field(default=256, ge=1, le=10000)
    web_login_protection: LoginProtection = Field(default_factory=LoginProtection)

    @field_validator("web_session_secret_file")
    @classmethod
    def secret_file_mode(cls, value: Path | None):
        if value is None:
            return value
        if value.stat().st_mode & 0o077:
            raise RuntimeError("会话签名密钥文件必须仅当前用户可读写")
        key = value.read_bytes().strip()
        if len(key) < 32:
            raise ValueError("会话签名密钥长度不足")
        return value

    @property
    def web_session_secret(self) -> bytes:
        if self.web_session_secret_file is None:
            raise RuntimeError("未配置 Web 会话签名密钥")
        return self.web_session_secret_file.read_bytes().strip()

    @field_validator("issuer", "audience", "public_url")
    @classmethod
    def safe_service_url(cls, value: str, info: ValidationInfo):
        url = urlparse(value)
        loopback = url.hostname in {"127.0.0.1", "localhost"}
        demo = False
        if info.data.get("allow_lan_http_demo") and url.hostname:
            try:
                address = ipaddress.ip_address(url.hostname)
                demo = address.version == 4 and any(
                    address in ipaddress.ip_network(net)
                    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
                )
            except ValueError:
                pass
        if (
            url.scheme != "https" and not (url.scheme == "http" and (loopback or demo))
        ) or not url.hostname:
            raise ValueError("服务地址须使用 HTTPS，仅本机开发允许 HTTP")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("服务地址不能包含凭证、查询或片段")
        return value.rstrip("/")

    @classmethod
    def load(cls, filename: str | None = None):
        path = filename or os.environ.get("OFFICE_CONFIG")
        if not path:
            raise RuntimeError("请通过 OFFICE_CONFIG 指定配置文件")
        config_file = Path(path).expanduser().resolve()
        data = json.loads(config_file.read_text())
        if isinstance(data, dict):
            for name in ("database_password_file", "web_session_secret_file"):
                value = data.get(name)
                if isinstance(value, str):
                    reference = Path(value).expanduser()
                    data[name] = (
                        reference if reference.is_absolute() else config_file.parent / reference
                    ).resolve()
            for key in ("identity_management", "token_introspection"):
                identity = data.get(key)
                if isinstance(identity, dict) and isinstance(
                    identity.get("client_secret_file"), str
                ):
                    reference = Path(identity["client_secret_file"]).expanduser()
                    identity["client_secret_file"] = (
                        reference if reference.is_absolute() else config_file.parent / reference
                    ).resolve()
        return cls.model_validate(data)
