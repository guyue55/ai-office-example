"""强类型外壳；模型不包含业务字段或权限逻辑。"""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def exact_int(value: object) -> int:
    if type(value) is not int:
        raise ValueError("必须为严格整数")
    return value


SuccessCode = Annotated[Literal[0], BeforeValidator(exact_int)]
PositiveCode = Annotated[int, Field(strict=True, gt=0, le=2147483647)]
SafeText = Annotated[str, Field(min_length=1, max_length=500)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
Index = Annotated[int, Field(strict=True, ge=0)]


def wire_schema(schema, model):
    if model.wire_fields:
        schema["required"] = list(dict.fromkeys([*schema.get("required", []), *model.wire_fields]))


class Model(BaseModel):
    wire_fields: ClassVar[tuple[str, ...]] = ()

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        revalidate_instances="always",
        json_schema_extra=wire_schema,
    )


class Issue(Model):
    path: Annotated[tuple[Identifier | Index, ...], Field(max_length=8)]
    reason: Literal[
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
    ]
    message: SafeText = "请核对该字段"


class Scope(Model):
    kind: Identifier
    ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=100)]


class Warning(Model):
    code: PositiveCode
    message: SafeText
    scope: Scope


class Recovery(Model):
    action: Literal[
        "correct_request",
        "reauthenticate",
        "check_permissions",
        "refresh",
        "replay_same_key",
        "query_operation",
        "retry_later",
        "contact_admin",
        "none",
    ]
    write_outcome: Literal["not_applied", "applied", "unknown", "not_applicable"]


class Meta(Model):
    wire_fields: ClassVar[tuple[str, ...]] = ("trace_id", "contract_version")
    trace_id: Identifier
    contract_version: Literal["1"] = "1"
    warnings: Annotated[tuple[Warning, ...], Field(max_length=20)] = ()
    recovery: Recovery | None = None
    replayed: bool = False
    details_truncated: bool = False


class Success[T](Model):
    wire_fields: ClassVar[tuple[str, ...]] = ("code", "message", "data", "meta")
    code: SuccessCode = 0
    message: SafeText = "操作成功"
    data: T
    meta: Meta


class Failure(Model):
    wire_fields: ClassVar[tuple[str, ...]] = ("code", "message", "data", "meta")
    code: PositiveCode
    message: SafeText
    data: None = None
    meta: Meta
    details: Annotated[tuple[Issue, ...], Field(max_length=20)] = ()


class ErrorInfo(Model):
    code: PositiveCode
    message: SafeText
