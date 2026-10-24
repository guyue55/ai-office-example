"""只约束未完成的登录发起；不按账号、设备数或已有会话限流。"""

import hashlib
import time
from collections import OrderedDict

from app.common.errors import DomainError


class LoginGate:
    def __init__(self, policy):
        self.policy = policy
        self.buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def admit_request(self, request, store) -> str:
        store.sweep()
        return self.admit(request.client.host if request.client else "unknown", store.pending)

    def admit(self, host: str, pending) -> str:
        # 只使用ASGI确认的连接来源；转发头须由启动入口的可信代理配置处理。
        source = hashlib.sha256(host.encode()).hexdigest()
        if (
            sum(value.get("_source") == source for value, _ in pending.values())
            >= self.policy.pending_per_source
        ):
            raise DomainError(
                503,
                "authentication_busy",
                "未完成的登录较多，请完成已有授权或稍后重试；现有登录不受影响",
            )
        now = time.monotonic()
        tokens, stamp = self.buckets.pop(source, (float(self.policy.burst), now))
        tokens = min(
            float(self.policy.burst), tokens + max(0, now - stamp) * self.policy.refill_per_second
        )
        allowed = tokens >= 1
        self.buckets[source] = (tokens - 1 if allowed else tokens, now)
        while len(self.buckets) > self.policy.max_sources:
            self.buckets.popitem(last=False)
        if not allowed:
            raise DomainError(
                503,
                "authentication_busy",
                "登录发起过于频繁，请稍后重试；其他设备的已有登录不受影响",
            )
        return source
