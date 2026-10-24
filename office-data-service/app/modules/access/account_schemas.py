"""账号管理的封闭命令，不接受realm、密码或任意上游字段。"""

from typing import Literal

from pydantic import Field, field_validator

from app.common.inputs import DateRangeInput, Identifier, Input, NumberedListInput, Revision


class AccountCreate(Input):
    kind: Literal["create"]
    name: str = Field(min_length=1, max_length=100)
    username: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
    email: str = Field(default="", max_length=254)
    role_ids: list[Identifier] | None = Field(default=None, max_length=100)
    mode: Literal["temporary", "email"] = "temporary"

    @field_validator("username")
    @classmethod
    def lowercase(cls, value):
        return value.lower()

    @field_validator("email")
    @classmethod
    def email_shape(cls, value):
        if value and (
            value.count("@") != 1
            or any(c.isspace() for c in value)
            or value.startswith("@")
            or value.endswith("@")
        ):
            raise ValueError("邮箱格式无效")
        return value


class AccountLink(Input):
    kind: Literal["link"]
    remote_id: Identifier
    name: str = Field(min_length=1, max_length=100)
    role_ids: list[Identifier] | None = Field(default=None, max_length=100)


class AccountTarget(Input):
    kind: Literal["disable", "enable", "logout"]
    identifier: Identifier
    version: Revision


class AccountReset(Input):
    kind: Literal["reset"]
    identifier: Identifier
    version: Revision
    mode: Literal["temporary", "email"] = "temporary"


class AccountUpdate(Input):
    kind: Literal["update"]
    identifier: Identifier
    version: Revision
    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("email")
    @classmethod
    def email_shape(cls, value):
        return AccountCreate.email_shape(value) if value is not None else value


AccountRequest = AccountCreate | AccountLink | AccountTarget | AccountReset | AccountUpdate


class AccountPreviewInput(Input):
    request: AccountRequest


class AccountSubmitInput(AccountPreviewInput):
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class AccountLookup(Input):
    identifier: Identifier


class AccountSearch(Input):
    q: str = Field(min_length=2, max_length=100)


class AccountContinuePreview(AccountLookup):
    mode: Literal["temporary", "email"] | None = None


class AccountResume(AccountLookup):
    mode: Literal["temporary", "email"] | None = None
    version: Revision
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class AccountHistory(NumberedListInput, DateRangeInput):
    identifier: Identifier | None = None
    q: str = Field(default="", max_length=100)
    status: Literal[
        "",
        "pending",
        "running",
        "awaiting_user",
        "succeeded",
        "needs_attention",
        "needs_confirmation",
    ] = ""
    kind: Literal["", "create", "link", "update", "disable", "enable", "reset", "logout"] = ""
