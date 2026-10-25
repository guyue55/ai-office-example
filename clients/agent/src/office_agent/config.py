"""可信配置、模型出口和有界预算；模型不能修改这些值。"""

import re
import tomllib
from pathlib import Path

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
    api_key_env: str
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
    def addresses(self):
        try:
            self.service_url = trusted_url(
                self.service_url, allow_lan_http_demo=self.allow_lan_http_demo
            )
            # 模型端点独立核验，企业HTTP演示开关不能扩大模型出口。
            self.model_url = trusted_url(self.model_url)
        except ClientError:
            raise ValueError("服务或模型端点不可信") from None
        if not self.approved_model_data:
            raise ValueError("请先确认该模型端点获准处理本任务数据")
        return self

    @classmethod
    def load(cls, path: str):
        try:
            source = Path(path)
            if source.is_symlink() or source.stat().st_size > 16000:
                raise ValueError("配置文件无效")
            return cls.model_validate(tomllib.loads(source.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError):
            # Pydantic异常包含输入值，不能原样展示。
            raise AgentError(
                "Agent配置无效：请核对服务、模型端点、密钥引用、数据许可和预算"
            ) from None
