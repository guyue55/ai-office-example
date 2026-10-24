"""被撤销的业务会话与普通过期令牌区分，Web可停止旧SSO自动回跳。"""

from app.common.errors import DomainError


class SessionEnded(DomainError):
    def __init__(self):
        super().__init__(401, "invalid_token", "此登录授权已结束，请退出旧登录后重新授权")
