"""身份管理的窄异步协议；业务侧不依赖第三方HTTP或身份产品模型。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LoginAccount:
    id: str
    username: str
    name: str
    email: str
    enabled: bool
    actions: tuple[str, ...]
    operation_id: str = ""


class IdentityAdmin(Protocol):
    async def search(self, query: str, limit: int = 20) -> list[LoginAccount]: ...
    async def get(self, identifier: str) -> LoginAccount: ...
    async def find_username(self, username: str) -> LoginAccount | None: ...
    async def create(
        self, username: str, name: str, email: str, operation_id: str
    ) -> LoginAccount: ...
    async def update(
        self,
        identifier: str,
        *,
        name: str | None = None,
        email: str | None = None,
        enabled: bool | None = None,
    ) -> None: ...
    async def temporary_password(self, identifier: str, password: str) -> None: ...
    async def password_email(self, identifier: str) -> None: ...
    async def end_sessions(self, identifier: str) -> None: ...
    async def end_session(self, identifier: str, session_id: str) -> None: ...
