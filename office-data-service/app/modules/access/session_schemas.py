"""授权会话契约：仅暴露业务编号和设备备注，不返回身份会话密钥。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.common.inputs import (
    DateRangeInput,
    Identifier,
    Input,
    NumberedListInput,
    PageLimit,
    Revision,
)
from app.common.outputs import AccessPage, Output


class SessionList(NumberedListInput, DateRangeInput):
    after: Annotated[str, Field(max_length=100)] = ""
    limit: PageLimit = 20
    scope: Literal["own", "all"] = "own"
    q: Annotated[str, Field(max_length=100)] = ""
    employee_id: Identifier | None = None
    state: Literal["", "active", "disabled", "revoked"] = ""
    client_id: Annotated[str, Field(max_length=200)] = ""
    archived: bool = False
    date_field: Literal["created_at", "last_seen_at"] = "created_at"


class SessionRegister(Input):
    device_name: Annotated[str, Field(max_length=100)] | None = None
    platform: Annotated[str, Field(max_length=100)] | None = None


class SessionChange(Input):
    identifier: Identifier
    version: Revision
    action: Literal["disable", "revoke", "archive", "retry"]
    reason: Annotated[str, Field(min_length=2, max_length=200)]


class SessionView(Output):
    id: str
    version: int
    employee_id: str
    employee_name: str
    clients: list[str]
    device_name: str
    platform: str
    state: Literal["active", "disabled", "revoked"]
    current: bool
    authenticated_at: datetime
    created_at: datetime
    last_seen_at: datetime
    token_expires_at: datetime
    ended_at: datetime | None
    archived_at: datetime | None
    cleanup: Literal["not_requested", "pending", "done", "failed"]


class SessionPage(AccessPage[SessionView]):
    pass
