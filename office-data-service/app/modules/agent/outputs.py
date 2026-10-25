"""助手任务与结果输出；不包含供应商密钥、OAuth或模型内部历史。"""

from datetime import datetime

from app.common.outputs import Output, Versioned
from app.modules.agent.schemas import RunStatus


class RunSummary(Versioned):
    prompt: str
    status: RunStatus
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime


class RunView(RunSummary):
    pending: dict | None
    result: dict | None
    write_inflight: bool


class AgentCapability(Output):
    installed: bool
    configured: bool
    ready: bool
    message: str
