"""助手任务与结果输出；不包含供应商密钥、OAuth或模型内部历史。"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.common.outputs import Output, Versioned
from app.modules.agent.schemas import RunStatus


class ProgressStep(Output):
    id: str = Field(max_length=80)
    operation: str = Field(max_length=80)
    status: Literal["running", "completed", "failed"]
    elapsed_ms: int = Field(ge=0)


class RunProgress(Output):
    schema_version: Literal[1] = 1
    seq: int = Field(ge=1)
    elapsed_ms: int = Field(ge=0)
    stage: Literal[
        "identity",
        "model",
        "reply",
        "tool",
        "awaiting_confirmation",
        "completed",
        "failed",
        "timeout",
        "budget_exceeded",
    ]
    text: str = Field(max_length=16000)
    steps: list[ProgressStep] = Field(max_length=64)
    truncated: bool = False


class RunSummary(Versioned):
    prompt: str
    status: RunStatus
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime


class RunView(RunSummary):
    progress: RunProgress | None = None
    model_revision: int | None = None
    conversation_id: str | None = None
    context_page: str = ""
    history_hidden: bool = False
    detail_truncated: bool = False
    pending: dict | None
    result: dict | None
    write_inflight: bool


class AgentCapability(Output):
    installed: bool
    configured: bool
    ready: bool
    message: str


class ConversationSummary(Versioned):
    title: str
    archived: bool
    created_at: datetime
    updated_at: datetime


class ConversationView(ConversationSummary):
    turns: list[RunView]
    has_more: bool
    next: str | None
    access_fingerprint: str
    history_reset: bool
