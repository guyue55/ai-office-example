"""输出契约基础件；严格检查额外字段，避免隐式泄露内部属性。"""

from pydantic import BaseModel, ConfigDict


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Versioned(Output):
    id: str
    version: int


class Associations(Versioned):
    ids: list[str]


class Page[T](Output):
    items: list[T]
    next: str | None


class Pagination(Output):
    """即时导航参考总数；空页start/end为0，不作为财务统计的一致性快照。"""

    page: int
    page_size: int
    total: int
    pages: int
    start: int
    end: int


class AccessPage[T](Page[T]):
    pagination: Pagination | None = None
    has_more: bool


class FieldIssue(Output):
    path: list[str]
    code: str
