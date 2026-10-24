"""通用输入策略与有界标识；JSON数字不隐式接受字符串或布尔值。"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def no_null_character(cls, value):
        strings = value if isinstance(value, list) else [value]
        if any(isinstance(item, str) and "\x00" in item for item in strings):
            raise ValueError("文本不能包含空字符")
        return value


Identifier = Annotated[str, Field(min_length=1, max_length=36)]
Cursor = Annotated[str, Field(max_length=100)]
PageLimit = Annotated[int, Field(strict=True, ge=1, le=100)]
Revision = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class EmptyInput(Input):
    pass


class ListInput(Input):
    after: Cursor = ""
    limit: PageLimit = 50


class IdentifierInput(Input):
    identifier: Identifier


class NumberedListInput(ListInput):
    page: Annotated[int, Field(strict=True, ge=1, le=2147483647)] | None = None

    @model_validator(mode="after")
    def pagination_mode(self):
        if self.page is not None and self.after:
            raise ValueError("页码与游标不能同时使用")
        return self


class DateRangeInput(Input):
    since: Annotated[str, Field(max_length=40)] = ""
    until: Annotated[str, Field(max_length=40)] = ""

    @model_validator(mode="after")
    def date_range(self):
        values = []
        for value in (self.since, self.until):
            if value:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    raise ValueError("日期必须包含时区")
                values.append(parsed)
        if self.since and self.until and values[0] >= values[1]:
            raise ValueError("结束时间必须晚于开始时间")
        return self
