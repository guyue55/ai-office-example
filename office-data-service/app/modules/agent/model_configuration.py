"""模型管理契约；秘密只允许写入，输出模型不包含秘密值或秘密引用。"""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator

from app.common.inputs import Input
from app.common.outputs import Output


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


class ModelDraft(Input):
    api: Literal["openai-completions", "openai-responses", "anthropic-messages"] = (
        "openai-completions"
    )
    model_url: str = Field(min_length=1, max_length=512)
    model: str = Field(min_length=1, max_length=200)
    anthropic_auth: Literal["bearer", "x-api-key"] = "bearer"
    request_timeout: float = Field(default=30, gt=0, le=300)
    approved_model_data: bool = False
    limits: ModelLimits = Field(default_factory=ModelLimits)

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
            or (
                url.scheme != "https"
                and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"})
            )
            or url.path.rstrip("/").endswith(("/chat/completions", "/responses", "/messages"))
        ):
            raise ValueError("请填写不含凭证的模型base URL，不能填写完整请求路径")
        if self.api != "anthropic-messages" and self.anthropic_auth != "bearer":
            raise ValueError("x-api-key仅适用于Anthropic协议")
        self.model_url = self.model_url.rstrip("/")
        return self


class ModelVersion(Input):
    version: int = Field(ge=0, le=2147483647)


class ModelSave(ModelVersion):
    draft: ModelDraft
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


class ModelApply(ModelVersion):
    revision: int = Field(ge=1)


class ModelTest(ModelApply):
    acknowledge_cost: Literal[True]


class ModelTestResult(Output):
    revision: int
    passed: bool
    message: str
    category: Literal[
        "ok",
        "authentication",
        "permission",
        "endpoint_or_model",
        "rate_limit",
        "provider",
        "dns",
        "tls",
        "connect",
        "timeout",
        "policy",
        "invalid_response",
    ]
    suggestion: str
    elapsed_ms: int = Field(ge=0)


class ModelTestView(Output):
    id: str
    revision: int
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
    message: str
