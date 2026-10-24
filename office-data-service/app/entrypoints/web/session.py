"""服务端会话存储；浏览器只持有签名引用 Cookie，令牌永不落浏览器。"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict

from app.common.errors import DomainError


class SessionError(Exception):
    pass


class LoginRequired(SessionError):
    pass


class SessionStore:
    """内存会话；服务重启即失效可接受，禁止把令牌编码进 Cookie。"""

    def __init__(self, secret: bytes, ttl_seconds: int, session_limit=2048, pending_limit=256):
        self.secret, self.ttl = secret, ttl_seconds
        self.session_limit, self.pending_limit = session_limit, pending_limit
        self.sessions: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self.pending: OrderedDict[str, tuple[dict, float]] = OrderedDict()

    def _put(self, mapping, limit, key, token, ttl):
        self.sweep()
        if len(mapping) >= limit:
            raise DomainError(503, "authentication_busy", "登录容量已满，请稍后重试")
        mapping[key] = (token, time.monotonic() + ttl)

    def put_pending(self, state, value):
        self._put(self.pending, self.pending_limit, state, value, 600)

    def _sign(self, reference: str) -> str:
        digest = hmac.new(self.secret, reference.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def create(self, token: dict) -> str:
        reference = secrets.token_urlsafe(32)
        self._put(self.sessions, self.session_limit, reference, token, self.ttl)
        return reference + "." + self._sign(reference)

    def load(self, cookie: str | None) -> dict:
        if not cookie or cookie.count(".") != 1:
            raise LoginRequired("未登录")
        reference, signature = cookie.split(".", 1)
        expected = self._sign(reference)
        if (
            not hmac.compare_digest(signature.encode(), expected.encode())
            or reference not in self.sessions
        ):
            raise LoginRequired("会话无效或已过期")
        token, deadline = self.sessions[reference]
        if time.monotonic() > deadline:
            del self.sessions[reference]
            raise LoginRequired("会话已过期")
        return token

    def destroy(self, cookie: str | None):
        if cookie and cookie.count(".") == 1:
            reference, signature = cookie.split(".", 1)
            if hmac.compare_digest(signature.encode(), self._sign(reference).encode()):
                self.sessions.pop(reference, None)

    def sweep(self):
        now = time.monotonic()
        # 两种存储各自固定TTL，插入顺序即过期顺序；不扫描仍有效的所有会话。
        for mapping in (self.sessions, self.pending):
            while mapping:
                key = next(iter(mapping))
                if mapping[key][1] > now:
                    break
                mapping.popitem(last=False)


def new_csrf() -> str:
    return secrets.token_urlsafe(24)
