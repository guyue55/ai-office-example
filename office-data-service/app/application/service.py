"""传输无关的调用边界：同一事务内校验身份、权限、数据并写审计。"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.application.account_service import Accounts
from app.application.capability_catalog import transport_coverage
from app.application.commands import validate_command
from app.application.contracts import OPERATION_OUTPUTS, validate_output
from app.application.data_changes import DataChanges
from app.application.session_service import Sessions
from app.common.errors import DomainError, validation_error
from app.common.metadata import SERVICE_VERSION
from app.common.security_context import actor_context
from app.infrastructure.models import Audit
from app.modules.access.changes import Changes
from app.modules.access.inspection import Inspection
from app.modules.access.management import Management
from app.modules.access.service import Access
from app.modules.data.lifecycle import Lifecycle
from app.modules.data.query import DataQuery
from app.modules.data.service import Data

logger = logging.getLogger("office.service")


class Application:
    def __init__(self, settings, sessions, verifier):
        self.settings, self.sessions, self.verifier = settings, sessions, verifier
        self._accounts = None
        self._agent = None

    def agent_service(self):
        if self._agent is None:
            from app.application.agent_service import AgentCoordinator

            self._agent = AgentCoordinator(self)
        return self._agent

    def account_service(self):
        if self._accounts is None:
            self._accounts = Accounts(self.settings, self.sessions)
        return self._accounts

    async def call(
        self,
        principal,
        operation: str,
        payload: dict,
        trace_id: str,
        *,
        presenter=None,
        authenticate=None,
    ):
        actor_token = actor_context.set(None)
        started = time.monotonic()
        outcome, code = "failed", "internal_error"
        try:
            result = await self._execute(
                principal,
                operation,
                payload,
                trace_id,
                presenter=presenter,
                authenticate=authenticate,
            )
            outcome, code = "succeeded", "ok"
            return result
        except DomainError as error:
            code = error.code
            actor = actor_context.get()
            if code == "permission_denied" and actor is not None:
                # 主业务事务已回滚；独立短事务只记录受控元信息，不记录请求或错误原文。
                try:
                    async with asyncio.timeout(2), self.sessions.begin() as security_db:
                        security_db.add(
                            Audit(
                                actor_id=actor,
                                action="security.denied",
                                resource_id=operation
                                if operation in OPERATION_OUTPUTS or operation == "access.list"
                                else "unsupported",
                                detail={"code": code},
                                trace_id=trace_id,
                            )
                        )
                except (TimeoutError, SQLAlchemyError):
                    logger.warning(
                        "[Trace: OfficeService/security/persist_failed] trace_id=%s", trace_id
                    )
            if code == "inactive_employee":
                # _execute 已回滚并释放主会话，不能在持有连接时再借连接留痕。
                try:
                    async with (
                        asyncio.timeout(self.settings.operation_timeout),
                        self.sessions.begin() as trace_session,
                    ):
                        await Access.trace_pending_identity(trace_session, principal)
                except (TimeoutError, IntegrityError, OperationalError, PoolTimeoutError):
                    # 留痕基础设施失败不改写原 403；不记录身份、SQL或凭证。
                    logger.warning(
                        "[Trace: OfficeService/access/pending_trace_failed] trace_id=%s", trace_id
                    )
            raise
        except asyncio.CancelledError:
            outcome, code = "cancelled", "request_cancelled"
            raise
        finally:
            # 不记录payload、异常原文或身份令牌；提交完成后才可能记录成功。
            known = (
                operation in {"access.list", "me", "capabilities"} or operation in OPERATION_OUTPUTS
            )
            logger.info(
                "[Trace: OfficeService/operation/end] %s",
                json.dumps(
                    {
                        "trace_id": trace_id,
                        "actor_id": actor_context.get(),
                        "operation": operation if known else "unsupported",
                        "outcome": outcome,
                        "code": code,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    },
                    ensure_ascii=False,
                ),
            )

            actor_context.reset(actor_token)

    async def _execute(
        self,
        principal,
        operation: str,
        payload: dict,
        trace_id: str,
        *,
        presenter=None,
        authenticate=None,
    ):
        def output(result):
            checked = validate_output(operation, payload, result)
            # 显式传入的准备器在事务退出前调用；v1/MCP原行为保持。
            return (
                presenter(checked, replayed=session.info.get("idempotency_replayed", False))
                if presenter is not None
                else checked
            )

        exclusive = (
            operation.startswith("data.change.")
            or operation.startswith("access.")
            and operation.split(".")[-1]
            in {
                "create",
                "update",
                "replace",
                "apply",
                "claim",
            }
        )
        try:
            payload = validate_command(operation, payload)
            if operation.startswith("agent."):
                return await self.agent_service().request(
                    principal,
                    operation,
                    payload,
                    trace_id,
                    presenter=presenter,
                    authenticate=authenticate,
                )
            if operation.startswith("session."):

                def session_output(result):
                    checked = validate_output(operation, payload, result)
                    return presenter(checked, replayed=False) if presenter is not None else checked

                async with asyncio.timeout(self.settings.operation_timeout):
                    return await Sessions(self.sessions, self.account_service().provider).request(
                        principal, operation, payload, trace_id, session_output
                    )
            if operation.startswith("account."):

                def account_output(result, *, replayed=False):
                    checked = validate_output(operation, payload, result)
                    return (
                        presenter(checked, replayed=replayed) if presenter is not None else checked
                    )

                async with asyncio.timeout(self.settings.operation_timeout):
                    return await self.account_service().request(
                        principal, operation, payload, trace_id, account_output
                    )
            async with (
                asyncio.timeout(self.settings.operation_timeout),
                self.sessions.begin() as session,
            ):
                access = await Access.create(session, principal, trace_id, exclusive)
                actor_context.set(access.actor.id)
                management, data = Management(access, self.settings.issuer), Data(access)
                if operation == "me":
                    return output(await management.me())
                if operation == "capabilities":
                    return output(
                        {
                            "transport_coverage": transport_coverage(),
                            "version": SERVICE_VERSION,
                            "identity_issuer": self.settings.issuer,
                            "field_types": [
                                "text",
                                "integer",
                                "decimal",
                                "date",
                                "boolean",
                                "enum",
                            ],
                            "query": "bounded-list",
                            "bulk": False,
                            "aggregation": True,
                            "scope": "global-ordinary-metadata/own-or-all-records",
                            "access_revision": access.revision.revision,
                        },
                    )
                lifecycle = Lifecycle(access, data)
                changes = Changes(management, self.settings.audience)
                data_changes = DataChanges(data, self.settings.audience)
                operations: dict[str, Callable[..., Awaitable[Any]]] = {
                    "data.aggregate": DataQuery(data).aggregate,
                    "data.change.preview": data_changes.preview,
                    "data.change.apply": data_changes.apply,
                    "access.list": management.listing,
                    "access.detail": Inspection(access).detail,
                    "access.create": management.create,
                    "access.employee.update": management.update_employee,
                    "access.links.replace": management.replace,
                    "access.links.get": management.associations,
                    "access.identity.claim": management.claim_identity,
                    "access.identity.pending": management.pending_identities,
                    "access.change.preview": changes.preview,
                    "access.change.apply": changes.apply,
                    "record.export.confirmed": data.export_confirmed,
                    "project.list": data.projects,
                    "project.create": data.create_project,
                    "project.update": data.update_project,
                    "schema.get": data.schema,
                    "schema.publish": data.publish_schema,
                    "record.list": data.records,
                    "record.create": data.create_record,
                    "record.get": data.get_record,
                    "record.update": data.update_record,
                    "record.archive": data.archive_record,
                    "record.confirm": lifecycle.confirm,
                    "record.correct": lifecycle.correct,
                    "record.confirmed": lifecycle.confirmed,
                    "record.history": lifecycle.history,
                }
                if operation not in operations:
                    raise DomainError(422, "unsupported_operation", "尚未提供此能力")
                return output(await operations[operation](**payload))
        except TimeoutError:
            raise DomainError(503, "operation_timeout", "操作超时，请稍后重试") from None
        except ValidationError as error:
            raise validation_error(error) from None
        except IntegrityError:
            raise DomainError(409, "constraint_conflict", "数据关系冲突或对象已存在") from None
        except (OperationalError, PoolTimeoutError):
            raise DomainError(503, "database_busy", "数据服务暂时不可用，请稍后重试") from None
