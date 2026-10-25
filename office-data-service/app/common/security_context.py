"""请求内已识别的业务人员；不包含令牌或未经验证的身份输入。"""

from contextvars import ContextVar

actor_context: ContextVar[str | None] = ContextVar("office_actor", default=None)
agent_run_context: ContextVar[str | None] = ContextVar("office_agent_run", default=None)
