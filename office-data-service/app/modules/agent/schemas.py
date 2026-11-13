"""任务输入；调用者不能提交模型历史、模型URL或替换待批准参数。"""

from typing import Literal

from pydantic import Field, field_validator

from app.common.inputs import DateRangeInput, IdentifierInput, Input, NumberedListInput
from app.common.text import TITLE_MAX_CHARS, normalize_title

RunStatus = Literal[
    "queued",
    "running",
    "awaiting_confirmation",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "outcome_unknown",
]


# 页面标签只描述讨论位置；不能指定URL、DOM或工具权限。
ContextPage = Literal[
    "",
    "dashboard",
    "employees",
    "roles",
    "permission-sets",
    "permissions",
    "audit",
    "client-authorizations",
    "assistant",
]


class RunStart(Input):
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    context_page: ContextPage = ""
    prompt: str = Field(min_length=1, max_length=16000)
    request_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class RunList(NumberedListInput, DateRangeInput):
    limit: int = Field(default=10, ge=1, le=100, strict=True)
    q: str = Field(default="", max_length=100)
    status: RunStatus | Literal[""] = ""


class RunDecision(IdentifierInput):
    version: int = Field(ge=1, strict=True)
    proposal_id: str = Field(min_length=1, max_length=100)
    approve: bool


class RunCancel(IdentifierInput):
    version: int = Field(ge=1, strict=True)


def _title(value):
    """标题一律先清洗再校验：转义、控制符、方向符与孤立代理项不得入库。

    清洗后为空则拒绝（等价于原来的"必须含非空白字符"），由调用方换成默认文案。
    """
    if isinstance(value, str):
        value = normalize_title(value)
        if not value:
            raise ValueError("标题不能为空或仅含不可见字符")
    return value


class ConversationCreate(Input):
    title: str = Field(default="新会话", min_length=1, max_length=TITLE_MAX_CHARS)

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, value):
        return _title(value)


class ConversationRename(IdentifierInput):
    version: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=TITLE_MAX_CHARS, pattern=r"\S")

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, value):
        return _title(value)


class ConversationList(NumberedListInput):
    q: str = Field(default="", max_length=100)
    limit: int = Field(default=10, ge=1, le=100, strict=True)
    archived: bool = False


class ConversationGet(IdentifierInput):
    before: str = Field(default="", max_length=36)
    limit: int = Field(default=10, ge=1, le=20, strict=True)


class ConversationArchive(IdentifierInput):
    version: int = Field(ge=1, strict=True)
    archived: bool
