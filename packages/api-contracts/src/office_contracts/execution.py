"""任务表示与状态转换检查；不提供持久任务或调度器。"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .collections import BatchResult
from .models import ErrorInfo, Identifier, Model


class Artifact(Model):
    id: Identifier
    availability: Literal["available", "expired", "unavailable"]


class Pending(Model):
    id: Identifier
    status: Literal["queued", "running", "cancel_requested"]


class Succeeded[T](Model):
    id: Identifier
    status: Literal["succeeded"] = "succeeded"
    result: T


class Failed(Model):
    id: Identifier
    status: Literal["failed"] = "failed"
    error: ErrorInfo


class Partial[T](Model):
    id: Identifier
    status: Literal["partial"] = "partial"
    result: BatchResult[T]

    @model_validator(mode="after")
    def partial_result(self):
        if self.result.status != "partial":
            raise ValueError("任务部分成功必须有部分成功的逐项结果")
        return self


class Cancelled(Model):
    id: Identifier
    status: Literal["cancelled"] = "cancelled"


type TaskResult[T] = Annotated[
    Pending | Succeeded[T] | Failed | Partial[T] | Cancelled, Field(discriminator="status")
]


def check_transition(previous: str, current: str):
    transitions = {
        "queued": {"queued", "running", "cancel_requested", "cancelled", "failed"},
        "running": {"running", "cancel_requested", "succeeded", "partial", "failed"},
        "cancel_requested": {"cancel_requested", "succeeded", "partial", "failed", "cancelled"},
        "succeeded": {"succeeded"},
        "partial": {"partial"},
        "failed": {"failed"},
        "cancelled": {"cancelled"},
    }
    if current not in transitions.get(previous, set()):
        raise ValueError("任务状态转换不被允许")
