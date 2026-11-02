"""基础持久化模型；用户身份和业务权限独立于身份产品。"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Entity(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))


class Employee(Entity):
    __tablename__ = "employee"
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    security_hold: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tokens_after: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, default=1)


class IdentityLink(Entity):
    __tablename__ = "identity_link"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employee.id"), index=True)
    managed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    login_name: Mapped[str | None] = mapped_column(String(100))
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(100))


class PendingIdentity(Entity):
    """未开通员工首次访问的留痕；认领后保留原记录供审计追溯。"""

    __tablename__ = "pending_identity"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claimed_by: Mapped[str | None] = mapped_column(ForeignKey("employee.id"), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Role(Entity):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("builtin_key", name="uq_role_builtin_key"),)
    builtin_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class PermissionSet(Entity):
    __tablename__ = "permission_set"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Permission(Base):
    __tablename__ = "permission"
    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(String(200))


class EmployeeRole(Base):
    __tablename__ = "employee_role"
    employee_id: Mapped[str] = mapped_column(ForeignKey("employee.id"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("role.id"), primary_key=True)


class RoleSet(Base):
    __tablename__ = "role_permission_set"
    role_id: Mapped[str] = mapped_column(ForeignKey("role.id"), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("permission_set.id"), primary_key=True)


class SetPermission(Base):
    __tablename__ = "permission_set_permission"
    set_id: Mapped[str] = mapped_column(ForeignKey("permission_set.id"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permission.code"), primary_key=True)


class AccessRevision(Base):
    __tablename__ = "access_revision"
    __table_args__ = (CheckConstraint("id = 1"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Audit(Entity):
    __tablename__ = "audit_event"
    actor_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Project(Entity):
    __tablename__ = "project"
    name: Mapped[str] = mapped_column(String(200))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    schema_revision: Mapped[int] = mapped_column(Integer, default=0)


class ProjectCreation(Base):
    """同一员工的同一创建请求只关联一个项目，不把项目姓名当业务唯一键。"""

    __tablename__ = "project_creation"
    actor_id: Mapped[str] = mapped_column(ForeignKey("employee.id"), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    request_digest: Mapped[str] = mapped_column(String(64))


class ProjectSchema(Base):
    __tablename__ = "project_schema"
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class Record(Entity):
    __tablename__ = "record"
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    schema_revision: Mapped[int] = mapped_column(Integer)
    owner_id: Mapped[str] = mapped_column(ForeignKey("employee.id"), index=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="submitted")
    current_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    correction_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # 同一员工重复提交相同幂等键时返回原记录，不隐式覆盖。
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_digest: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["project_id", "schema_revision"],
            ["project_schema.project_id", "project_schema.revision"],
        ),
    )


class RecordConfirmation(Base):
    """冻结的确认版本；运行账号仅可查询和追加。"""

    __tablename__ = "record_confirmation"
    record_id: Mapped[str] = mapped_column(ForeignKey("record.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_revision: Mapped[int] = mapped_column(Integer)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    __table_args__ = (CheckConstraint("revision >= 1", name="ck_confirmation_revision"),)


class IdentityOperation(Entity):
    """可恢复账号操作；request永不接受密码或管理令牌。"""

    __tablename__ = "identity_operation"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_identity_operation_key"),
        Index("ix_identity_operation_employee_created", "employee_id", "created_at", "id"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employee.id"), nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), index=True)
    step: Mapped[str] = mapped_column(String(40))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(String(240), default="")
    trace_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=1)
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    access_revision: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClientAuthorization(Entity):
    """授权会话索引及拒绝记录；秘密始终由身份服务/系统钥匙串持有。"""

    __tablename__ = "client_authorization"
    __table_args__ = (
        UniqueConstraint("issuer", "session_id", name="uq_client_authorization_session"),
        CheckConstraint(
            "state IN ('active','disabled','revoked')", name="ck_client_authorization_state"
        ),
        Index("ix_client_authorization_employee_created", "employee_id", "created_at", "id"),
        Index("ix_client_authorization_created", "created_at", "id"),
    )
    issuer: Mapped[str] = mapped_column(String(512))
    session_id: Mapped[str] = mapped_column(String(200))
    employee_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    clients: Mapped[list[str]] = mapped_column(JSONB)
    device_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    platform: Mapped[str] = mapped_column(String(100), default="", server_default="")
    state: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup: Mapped[str] = mapped_column(
        String(20), default="not_requested", server_default="not_requested"
    )


class AgentRun(Entity):
    """服务端任务权威记录；不保存模型密钥、OAuth或可重放模型历史。"""

    __tablename__ = "agent_run"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_agent_run_request"),
        CheckConstraint(
            "status IN ('queued','running','awaiting_confirmation','completed','failed',"
            "'cancelled','interrupted','outcome_unknown')",
            name="ck_agent_run_status",
        ),
        Index("ix_agent_run_owner_created", "owner_id", "created_at", "id"),
    )
    model_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_conversation.id"), nullable=True, index=True
    )
    context_page: Mapped[str] = mapped_column(String(40), default="", server_default="")
    access_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    request_key: Mapped[str] = mapped_column(String(100))
    request_digest: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(String(16000))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    pending: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    write_inflight: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    trace_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentConversation(Entity):
    """本人会话；每轮消息和回执沿用AgentRun，不建立第二份执行事实。"""

    __tablename__ = "agent_conversation"
    __table_args__ = (Index("ix_agent_conversation_owner_updated", "owner_id", "updated_at", "id"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    title: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelConfiguration(Base):
    """唯一配置选择器；停用不回退外部文件，修订以不可变记录保留。"""

    __tablename__ = "model_configuration"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    draft_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="external")


class ModelConfigurationRevision(Base):
    __tablename__ = "model_configuration_revision"
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB)
    secret_ciphertext: Mapped[str | None] = mapped_column(String(12000), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelConnectionTest(Entity):
    __tablename__ = "model_connection_test"
    __table_args__ = (
        Index("ix_model_connection_test_created", "created_at", "id"),
        Index("uq_model_test_actor_request", "actor_id", "request_key", unique=True),
    )
    revision: Mapped[int | None] = mapped_column(
        ForeignKey("model_configuration_revision.revision"), nullable=True
    )
    request_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("employee.id"))
    api: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), default="started")
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
