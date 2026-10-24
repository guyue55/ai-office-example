"""协议接收模型允许兼容扩展，业务T仍遵守自己声明的约束。"""

from typing import Annotated

from pydantic import ConfigDict, Field, field_validator

from ..models import Failure, Issue, Meta, Recovery, Scope, Success, Warning


class ReadScope(Scope):
    model_config = ConfigDict(extra="ignore", strict=True)


class ReadRecovery(Recovery):
    model_config = ConfigDict(extra="ignore", strict=True)


class ReadWarning(Warning):
    model_config = ConfigDict(extra="ignore", strict=True)
    scope: ReadScope


class ReadIssue(Issue):
    @field_validator("reason", mode="before")
    @classmethod
    def unknown_reason(cls, value):
        known = {
            "required",
            "null_not_allowed",
            "invalid_type",
            "out_of_range",
            "unknown_field",
            "unknown_parameter",
            "duplicate_parameter",
            "duplicate_key",
            "invalid_format",
            "invalid_choice",
            "conflict",
            "invalid",
        }
        return "invalid" if isinstance(value, str) and value not in known else value

    model_config = ConfigDict(extra="ignore", strict=True)


class ReadMeta(Meta):
    model_config = ConfigDict(extra="ignore", strict=True)
    warnings: Annotated[tuple[ReadWarning, ...], Field(max_length=20)] = ()
    recovery: ReadRecovery | None = None


class ReadSuccess[T](Success[T]):
    model_config = ConfigDict(extra="ignore", strict=True)
    meta: ReadMeta


class ReadFailure(Failure):
    model_config = ConfigDict(extra="ignore", strict=True)
    meta: ReadMeta
    details: Annotated[tuple[ReadIssue, ...], Field(max_length=20)] = ()
