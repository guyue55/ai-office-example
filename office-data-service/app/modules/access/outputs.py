"""身份和权限管理的公共返回结构，不携带来源令牌或数据库关系对象。"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.common.outputs import AccessPage, Output, Versioned
from app.modules.access.change_contracts import Change


class EmployeeOutput(Versioned):
    name: str
    active: bool


class MyRoleOutput(Output):
    """本人角色摘要：只描述自己被授予的角色，不暴露其他成员信息。"""

    id: str
    name: str
    builtin_key: str | None = None
    permission_count: int


class IdentityOutput(EmployeeOutput):
    permissions: list[str]
    access_revision: int
    permission_details: list["PermissionDetail"] = Field(default_factory=list)
    roles: list[MyRoleOutput] = Field(default_factory=list)


class NamedOutput(Versioned):
    name: str


class EmployeeListOutput(EmployeeOutput):
    role_count: int
    login_name: str | None = None
    account_status: str = ""
    managed: bool = False


class RoleOutput(NamedOutput):
    builtin_key: str | None = None
    permission_set_count: int
    permission_count: int
    employee_count: int


class PermissionSetOutput(NamedOutput):
    permission_count: int
    role_count: int


class PermissionOutput(Output):
    code: str
    description: str


class PermissionDetail(PermissionOutput):
    name: str
    group: str


class AccessReference(Output):
    id: str
    name: str
    description: str = ""


class AccessDetail(NamedOutput):
    kind: Literal["employees", "roles", "permission-sets"]
    builtin_key: str | None = None
    active: bool | None
    links: list[AccessReference]
    permissions: list[PermissionDetail]
    used_by: AccessPage[AccessReference]


class AuditOutput(Output):
    id: str
    actor_id: str
    actor_name: str = ""
    action_name: str = ""
    action: str
    resource_id: str
    resource_name: str = ""
    detail: dict
    detail_summary: str = ""
    trace_id: str
    created_at: datetime


class PendingIdentityOutput(Output):
    id: str
    display_name: str
    subject: str
    first_seen_at: datetime
    last_seen_at: datetime


class PendingIdentityPage(Output):
    items: list[PendingIdentityOutput]


class AccessListing(
    AccessPage[
        EmployeeListOutput | RoleOutput | PermissionSetOutput | PermissionOutput | AuditOutput
    ]
):
    """MCP 保留对象型分页返回，避免联合根类型被协议SDK额外包装。"""


class CapabilitiesOutput(Output):
    transport_coverage: dict[str, dict] = Field(default_factory=dict)
    data_change_confirmation: Literal["preview-digest"] = "preview-digest"
    confirmed_export: bool = True
    identity_issuer: str = ""
    access_management: bool = True
    access_change_confirmation: Literal["preview-digest"] = "preview-digest"
    record_lifecycle: bool = True
    version: str
    field_types: list[str]
    query: Literal["bounded-list"]
    bulk: Literal[False]
    aggregation: Literal[True]
    scope: str
    access_revision: int


class ChangePreview(Output):
    change: Change
    confirmation_digest: str
    target: AccessReference | None
    before: dict
    after: dict
    added: list[AccessReference]
    removed: list[AccessReference]
    used_by: AccessPage[AccessReference]
    access_revision: int
    notice: str


class ProvisionedOutput(Versioned):
    assigned_role: AccessReference | None = None


class ChangeResult(ProvisionedOutput):
    operation: str
    ids: list[str] | None
