"""管理变更的封闭输入集合；工具只能调用已实现的业务操作。"""

from typing import Literal

from pydantic import Field

from app.common.inputs import Identifier, Input
from app.modules.access.schemas import (
    EmployeeUpdate,
    GrantUpdate,
    Links,
    Named,
    NewEmployee,
    PendingClaim,
)

Kind = Literal["employees", "roles", "permission-sets"]


class CreatePayload(Input):
    kind: Kind
    body: NewEmployee | Named


class EmployeePayload(Input):
    identifier: Identifier
    body: EmployeeUpdate


class LinksPayload(Input):
    kind: Kind
    identifier: Identifier
    relation: Literal["roles", "permission-sets", "permissions"]
    body: GrantUpdate | Links


class CreateChange(Input):
    operation: Literal["access.create"]
    payload: CreatePayload


class EmployeeChange(Input):
    operation: Literal["access.employee.update"]
    payload: EmployeePayload


class LinksChange(Input):
    operation: Literal["access.links.replace"]
    payload: LinksPayload


class ClaimChange(Input):
    operation: Literal["access.identity.claim"]
    payload: PendingClaim


Change = CreateChange | EmployeeChange | LinksChange | ClaimChange


class PreviewChangeInput(Input):
    change: Change


class ApplyChangeInput(PreviewChangeInput):
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
