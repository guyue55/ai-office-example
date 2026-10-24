"""账号管理公共返回；明确处理状态，不携带任何秘密。"""

from datetime import datetime
from typing import Literal

from app.common.outputs import AccessPage, Output, Versioned
from app.modules.access.account_schemas import AccountRequest


class AccountCapability(Output):
    enabled: bool
    email_enabled: bool


class AccountInfo(Output):
    id: str
    username: str
    name: str
    email: str
    enabled: bool
    linked_employee_id: str | None = None


class AccountSearchResult(Output):
    items: list[AccountInfo]
    notice: str = "最多显示20项，请细化关键词核对唯一账号"


class AccountPreview(Output):
    request: AccountRequest
    confirmation_digest: str
    access_revision: int
    effects: list[str]


class AccountOperationView(Versioned):
    employee_name: str
    kind: Literal["create", "link", "update", "disable", "enable", "reset", "logout"]
    status: Literal[
        "pending", "running", "awaiting_user", "succeeded", "needs_attention", "needs_confirmation"
    ]
    step: str
    message: str
    employee_id: str | None
    username: str
    next_url: str
    created_at: datetime
    updated_at: datetime


class AccountDetail(Output):
    employee_id: str
    version: int
    name: str
    active: bool
    security_pending: bool
    account: AccountInfo | None
    available: bool
    notice: str


class AccountHistoryPage(AccessPage[AccountOperationView]):
    pass
