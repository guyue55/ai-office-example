"""请求关联号跨HTTP与MCP上下文传递，不携带业务资料或身份令牌。"""

from contextvars import ContextVar

trace_context: ContextVar[str | None] = ContextVar("office_trace", default=None)
