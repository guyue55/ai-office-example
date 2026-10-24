"""可复用的动态标量字段合同；不包含项目、指标或确认流程。"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.common.errors import require
from app.common.inputs import Input

FieldIdentifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
FieldType = Literal["text", "integer", "decimal", "date", "boolean", "enum"]


class FieldDefinition(Input):
    id: FieldIdentifier
    label: str = Field(min_length=1, max_length=100)
    type: FieldType
    required: bool = False
    description: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_options(self):
        if self.type == "enum" and (
            not self.options or len(set(self.options)) != len(self.options)
        ):
            raise ValueError("枚举必须有非重复选项")
        if self.type != "enum" and self.options:
            raise ValueError("只有枚举可以设置选项")
        return self


def validate_values(fields: list[dict], values: dict) -> dict:
    known = {item["id"]: item for item in fields}
    require(
        not set(values) - set(known),
        422,
        "unknown_field",
        "包含未定义字段",
        details=[{"path": ["values"], "code": "unknown_field"}],
    )
    result = {}
    for key, field in known.items():
        value = values.get(key)
        if value is None:
            require(
                not field["required"],
                422,
                "missing_field",
                "缺少必填字段：" + key,
                details=[{"path": ["values", key], "code": "missing_field"}],
            )
            continue
        kind = field["type"]
        valid = False
        if kind == "text":
            valid = isinstance(value, str) and len(value) <= 4000 and "\x00" not in value
        elif kind == "integer":
            valid = type(value) is int and -(2**63) <= value < 2**63
        elif kind == "boolean":
            valid = type(value) is bool
        elif kind == "enum":
            valid = isinstance(value, str) and value in field["options"]
        elif kind == "date":
            try:
                valid = isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
                if valid:
                    date.fromisoformat(value)
            except ValueError:
                valid = False
        elif kind == "decimal":
            # 十进制数值使用字符串，拒绝浮点与非有限值。
            try:
                valid = (
                    isinstance(value, str)
                    and len(value) <= 40
                    and bool(re.fullmatch(r"-?\d{1,24}(\.\d{1,8})?", value))
                )
                if valid:
                    valid = Decimal(value).is_finite()
            except InvalidOperation:
                valid = False
        require(
            valid,
            422,
            "invalid_field",
            "字段类型或取值不符合定义：" + key,
            details=[{"path": ["values", key], "code": "invalid_field"}],
        )
        result[key] = value
    return result
