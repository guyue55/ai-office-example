"""可信配置、模型出口和有界预算；模型不能修改这些值。"""

import re
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from office_client.auth import ClientError, trusted_url
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .policy import AgentError


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requests: int = Field(default=8, ge=1, le=50)
    tools: int = Field(default=20, ge=1, le=100)
    pages: int = Field(default=10, ge=1, le=10)
    seconds: int = Field(default=120, ge=1, le=600)
    tokens: int = Field(default=32000, ge=256, le=200000)
    input_bytes: int = Field(default=16000, ge=128, le=100000)
    result_bytes: int = Field(default=32000, ge=128, le=200000)
    output_bytes: int = Field(default=32000, ge=128, le=100000)
    pending_writes: int = Field(default=8, ge=1, le=20)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service_url: str
    model: str = Field(min_length=1, max_length=200)
    model_url: str
    api: Literal["openai-completions", "openai-responses", "anthropic-messages"] = (
        "openai-completions"
    )
    anthropic_auth: Literal["bearer", "x-api-key"] = "bearer"
    auth_mode: Literal["api-key", "none", "custom-header"] = "api-key"
    auth_header: str = Field(default="x-api-key", min_length=1, max_length=100)
    extra_headers: dict[str, str] = Field(default_factory=dict, max_length=16)
    extra_query: dict[str, str] = Field(default_factory=dict, max_length=16)
    allow_model_http: bool = False
    stream_output: bool = True
    request_timeout: float = Field(default=30, gt=0, le=300)
    api_key_env: str = "OFFICE_MODEL_API_KEY"
    api_key_file: str | None = Field(default=None, min_length=1, max_length=4096)
    # 配置者应先确认企业数据政策；不将开关解释成自动脱敏或法律合规证明。
    approved_model_data: bool = False
    allow_lan_http_demo: bool = False
    limits: Limits = Field(default_factory=Limits)

    @field_validator("api_key_env")
    @classmethod
    def secret_reference(cls, value):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", value):
            raise ValueError("模型密钥只接受环境变量名称，不接受密钥值")
        return value

    @model_validator(mode="after")
    def gateway_options(self):
        # 普通配置仅放路由／版本等非秘密参数；秘密继续使用独立凭证通道。
        reserved = {
            "host",
            "connection",
            "content-length",
            "content-type",
            "transfer-encoding",
            "accept-encoding",
            "cookie",
            "set-cookie",
            "proxy-authorization",
            "authorization",
        }

        def valid_name(name):
            return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,99}", name))

        def sensitive(name):
            return any(
                word in name.lower()
                for word in (
                    "key",
                    "token",
                    "secret",
                    "password",
                    "credential",
                    "authorization",
                    "cookie",
                )
            )

        if not valid_name(self.auth_header) or self.auth_header.lower() in reserved:
            raise ValueError("自定义凭证头名称无效或属于传输层保留头")
        for values in (self.extra_headers, self.extra_query):
            lowered = [name.lower() for name in values]
            if len(set(lowered)) != len(lowered):
                raise ValueError("网关参数不能大小写重复")
            for name, value in values.items():
                if (
                    not valid_name(name)
                    or sensitive(name)
                    or name.lower() in reserved
                    or name.lower() == self.auth_header.lower()
                ):
                    raise ValueError("网关参数不能包含凭证或覆盖保留头")
                if len(value) > 256 or any(ord(c) < 32 or ord(c) > 126 for c in value):
                    raise ValueError("网关参数只接受有界可打印ASCII值")
        return self

    @model_validator(mode="after")
    def addresses(self):
        if self.api != "anthropic-messages" and self.anthropic_auth != "bearer":
            raise ValueError("旧Anthropic鉴权头仅适用于Messages协议")
        try:
            self.service_url = trusted_url(
                self.service_url, allow_lan_http_demo=self.allow_lan_http_demo
            )
            # 模型端点独立核验，企业HTTP演示开关不能扩大模型出口。
            self.model_url = trusted_url(self.model_url, allow_lan_http_demo=self.allow_model_http)
        except ClientError:
            raise ValueError("服务或模型端点不可信") from None
        if (
            urlsplit(self.model_url)
            .path.rstrip("/")
            .endswith(("/chat/completions", "/responses", "/messages"))
        ):
            raise ValueError("请填写模型base URL，不填完整请求路径")
        if not self.approved_model_data:
            raise ValueError("请先确认该模型端点获准处理本任务数据")
        return self

    @classmethod
    def load(cls, path: str):
        try:
            source = Path(path)
            if source.is_symlink() or source.stat().st_size > 16000:
                raise ValueError("配置文件无效")
            data = tomllib.loads(source.read_text(encoding="utf-8-sig"))
            if isinstance(data.get("api_key_file"), str):
                secret = Path(data["api_key_file"])
                if not secret.is_absolute():
                    data["api_key_file"] = str(source.absolute().parent / secret)
            return cls.model_validate(data)
        except (OSError, ValueError):
            # Pydantic异常包含输入值，不能原样展示。
            raise AgentError(
                "Agent配置无效：请核对服务、API协议、模型端点、密钥引用、数据许可和预算"
            ) from None
