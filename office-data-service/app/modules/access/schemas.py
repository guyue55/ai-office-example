"""纯人员授权输入模型与局部规范化；不依赖数据库或应用总操作注册。"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.common.errors import DomainError
from app.common.inputs import (
    DateRangeInput,
    EmptyInput,
    IdentifierInput,
    Input,
    NumberedListInput,
    Revision,
)


class Named(Input):
    name: str = Field(min_length=1, max_length=100)


class NewEmployee(Named):
    assign_default_role: bool = True
    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=100)


class EmployeeUpdate(Input):
    version: Revision
    name: str = Field(min_length=1, max_length=100)
    active: bool


class PendingClaim(Input):
    """认领待认领登录：同时确定业务姓名并建立身份绑定。"""

    assign_default_role: bool = True
    pending_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=100)


class PendingList(EmptyInput):
    """待认领登录仅按时间顺序展示，无需分页参数。"""


class Links(Input):
    version: Revision
    ids: list[str] = Field(max_length=100)


class GrantUpdate(Input):
    version: Revision
    permissions: list[str] = Field(max_length=100)


class AccessList(NumberedListInput, DateRangeInput):
    kind: Literal["employees", "roles", "permission-sets", "permissions", "audit"]
    q: str = Field(default="", max_length=100)
    actor: str = Field(default="", max_length=100)
    action: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def audit_filters(self):
        if self.kind != "audit" and any((self.actor, self.action, self.since, self.until)):
            raise ValueError("操作者、动作和日期筛选仅用于审计")
        return self


class AccessDetailInput(IdentifierInput, NumberedListInput):
    kind: Literal["employees", "roles", "permission-sets"]


class AccessCreate(Input):
    kind: Literal["employees", "roles", "permission-sets"]
    body: dict


class EmployeeCommand(IdentifierInput):
    body: EmployeeUpdate


class AssociationsInput(IdentifierInput):
    kind: Literal["employees", "roles", "permission-sets"]
    relation: Literal["roles", "permission-sets", "permissions"]


class ReplaceInput(AssociationsInput):
    body: dict


ACCESS_INPUTS: dict[str, type[BaseModel]] = {
    "access.list": AccessList,
    "access.detail": AccessDetailInput,
    "access.create": AccessCreate,
    "access.employee.update": EmployeeCommand,
    "access.links.get": AssociationsInput,
    "access.links.replace": ReplaceInput,
    "access.identity.claim": PendingClaim,
    "access.identity.pending": PendingList,
}


def normalize_access_command(operation: str, payload: dict):
    if operation not in ACCESS_INPUTS:
        raise DomainError(422, "unsupported_operation", "尚未提供此能力")
    result = ACCESS_INPUTS[operation].model_validate(payload).model_dump(exclude_unset=True)
    if operation == "access.create":
        model: type[BaseModel] = NewEmployee if result["kind"] == "employees" else Named
        result["body"] = model.model_validate(result["body"]).model_dump()
    if operation.startswith("access.links."):
        if (result["kind"], result["relation"]) not in {
            ("employees", "roles"),
            ("roles", "permission-sets"),
            ("permission-sets", "permissions"),
        }:
            raise DomainError(404, "not_found", "关联资源不存在")
        if operation.endswith("replace"):
            model = GrantUpdate if result["kind"] == "permission-sets" else Links
            result["body"] = model.model_validate(result["body"]).model_dump()
    return result
