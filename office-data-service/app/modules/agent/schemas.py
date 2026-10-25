"""任务输入；调用者不能提交模型历史、模型URL或替换待批准参数。"""

from typing import Literal

from pydantic import Field

from app.common.inputs import DateRangeInput, IdentifierInput, Input, NumberedListInput

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


class RunStart(Input):
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
