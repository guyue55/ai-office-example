"""模型管理契约；秘密只允许写入，输出模型不包含秘密值或秘密引用。"""

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator

from app.common.inputs import Input
from app.common.outputs import Output

ModelCheckCategory = Literal[
    "ok",
    "authentication",
    "permission",
    "endpoint_or_model",
    "rate_limit",
    "provider",
    "dns",
    "dns_fake_ip",
    "dns_address",
    "tls",
    "connect",
    "timeout",
    "policy",
    "invalid_response",
    "response_limit",
]


class ModelLimits(Input):
    requests: int = Field(default=8, ge=1, le=50)
    tools: int = Field(default=20, ge=1, le=100)
    pages: int = Field(default=10, ge=1, le=10)
    seconds: int = Field(default=120, ge=1, le=600)
    tokens: int = Field(default=32000, ge=256, le=200000)
    input_bytes: int = Field(default=16000, ge=128, le=100000)
    result_bytes: int = Field(default=32000, ge=128, le=200000)
    output_bytes: int = Field(default=32000, ge=128, le=100000)
    pending_writes: int = Field(default=8, ge=1, le=20)


class ModelConnection(Input):
    api: Literal["openai-completions", "openai-responses", "anthropic-messages"] = (
        "openai-completions"
    )
    model_url: str = Field(min_length=1, max_length=512)
    anthropic_auth: Literal["bearer", "x-api-key"] = "bearer"
    auth_mode: Literal["api-key", "none", "custom-header"] = "api-key"
    auth_header: str = Field(default="x-api-key", min_length=1, max_length=100)
    extra_headers: dict[str, str] = Field(default_factory=dict, max_length=16)
    extra_query: dict[str, str] = Field(default_factory=dict, max_length=16)
    request_timeout: float = Field(default=30, gt=0, le=300)

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
    def endpoint(self):
        url = urlsplit(self.model_url)
        if (
            any(c.isspace() or ord(c) < 32 for c in self.model_url)
            or url.port == 0
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.scheme not in {"http", "https"}
            or url.path.rstrip("/").endswith(("/chat/completions", "/responses", "/messages"))
        ):
            raise ValueError("请填写不含凭证的模型base URL，不能填写完整请求路径")
        if self.api != "anthropic-messages" and self.anthropic_auth != "bearer":
            raise ValueError("x-api-key仅适用于Anthropic协议")
        self.model_url = self.model_url.rstrip("/")
        return self


class ModelDraft(ModelConnection):
    model: str = Field(min_length=1, max_length=200)
    approved_model_data: bool = False
    limits: ModelLimits = Field(default_factory=ModelLimits)


class ModelVersion(Input):
    version: int = Field(ge=0, le=2147483647)


class ModelCredentials(ModelVersion):
    reuse_secret_at_new_url: bool = False
    secret_action: Literal["keep", "replace", "clear"] = "keep"
    secret: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)

    @model_validator(mode="after")
    def secret_command(self):
        if (self.secret_action == "replace") != (self.secret is not None):
            raise ValueError("替换时需提供新密钥，其余动作不得携带密钥")
        if self.secret is not None and any(
            c.isspace() or ord(c) < 32 for c in self.secret.get_secret_value()
        ):
            raise ValueError("密钥不能包含空白或控制字符")
        return self


class ModelSave(ModelCredentials):
    draft: ModelDraft


class ModelDiscover(ModelCredentials):
    connection: ModelConnection


class ModelProbe(ModelSave):
    acknowledge_cost: Literal[True]
    request_key: str = Field(min_length=1, max_length=100)


class ModelChoice(Output):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(max_length=200)


class ModelDiscoveryResult(Output):
    passed: bool
    category: ModelCheckCategory
    message: str
    suggestion: str
    elapsed_ms: int = Field(ge=0)
    items: list[ModelChoice] = Field(max_length=1000)
    truncated: bool


class ModelApply(ModelVersion):
    revision: int = Field(ge=1)


class ModelTest(ModelApply):
    acknowledge_cost: Literal[True]


class ModelTestResult(Output):
    revision: int | None
    passed: bool
    message: str
    category: ModelCheckCategory
    suggestion: str
    elapsed_ms: int = Field(ge=0)


class ModelTestView(Output):
    id: str
    revision: int | None
    api: str
    model: str
    state: Literal["started", "completed", "interrupted"]
    created_at: datetime
    result: ModelTestResult | None


class ModelStatus(Output):
    version: int
    draft_revision: int | None
    active_revision: int | None
    source: Literal["external", "managed", "disabled"]
    installed: bool
    ready: bool
    secret_store_ready: bool
    secret_set: bool
    draft: ModelDraft | None
    active: ModelDraft | None = None
    url_policy: Literal["public", "allowlist"] = "public"
    last_test: ModelTestView | None = None
    allowed_urls: list[str]
    test_timeout_seconds: float = Field(default=30, gt=0, le=30)
    message: str
