"""分页和独立批次仅表示结果，不执行查询或事务。"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import ErrorInfo, Identifier, Model, SafeText, SuccessCode


class Page[T](Model):
    items: tuple[T, ...]
    next: str | None


class BatchSuccess[T](Model):
    id: Identifier
    status: Literal["succeeded"] = "succeeded"
    code: SuccessCode
    message: SafeText
    data: T


class BatchFailure(ErrorInfo):
    id: Identifier
    status: Literal["failed"] = "failed"
    data: None


class NotExecuted(ErrorInfo):
    id: Identifier
    status: Literal["not_executed"] = "not_executed"
    data: None


type BatchItem[T] = Annotated[
    BatchSuccess[T] | BatchFailure | NotExecuted, Field(discriminator="status")
]


class BatchResult[T](Model):
    status: Literal["succeeded", "partial", "failed"]
    items: Annotated[tuple[BatchItem[T], ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def consistent(self):
        ids = [item.id for item in self.items]
        succeeded = sum(item.status == "succeeded" for item in self.items)
        expected = "succeeded" if succeeded == len(ids) else "partial" if succeeded else "failed"
        if len(set(ids)) != len(ids) or self.status != expected:
            raise ValueError("批次标识或汇总状态不一致")
        return self

    def check_request_ids(self, ids: tuple[str, ...]):
        if len(set(ids)) != len(ids) or set(ids) != {item.id for item in self.items}:
            raise ValueError("批次结果与请求项不对应")
