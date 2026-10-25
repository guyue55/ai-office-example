"""工具桥：固定SDK白名单、精确预览、一次性意图和未知结果熔断。"""

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from .bridge import ToolBridge
from .catalog import READS, WRITES
from .config import Limits
from .policy import SECRET_KEYS, AgentError, ToolFailure, encoded, sanitize


@dataclass(frozen=True)
class Proposal:
    ticket: str
    operation: str
    payload_json: str
    preview_json: str
    created_at: float

    @property
    def arguments(self):
        return json.loads(self.payload_json)

    @property
    def preview(self):
        return json.loads(self.preview_json)


def validate_arguments(spec, arguments, limit):
    encoded(arguments, limit)
    if not isinstance(arguments, dict) or set(arguments) - set(spec.arguments):
        raise AgentError("工具参数包含未知字段")
    if set(spec.required) - set(arguments):
        raise AgentError("工具缺少必要参数：" + ",".join(spec.required))
    for key in ("limit", "max_records", "page"):
        value = arguments.get(key)
        maximum = 1000 if key == "max_records" else 100
        if value is not None and (type(value) is not int or not 1 <= value <= maximum):
            raise AgentError("分页或导出预算无效")

    def no_secrets(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower().replace("-", "_") in SECRET_KEYS:
                    raise AgentError("工具参数不能包含密码或令牌")
                no_secrets(item)
        elif isinstance(value, list):
            for item in value:
                no_secrets(item)

    no_secrets(arguments)
    return json.loads(encoded(arguments, limit))


class Gateway:
    def __init__(self, bridge: ToolBridge, limits: Limits):
        self.bridge, self.limits = bridge, limits
        self.identity: tuple[str, int] | None = None
        self.proposals: dict[str, Proposal] = {}
        self.used: set[str] = set()
        self.pages = 0
        self.receipts: list[dict] = []
        self.outcome_unknown = False

    async def _call(self, method, *, writing=False, **arguments):
        if self.outcome_unknown:
            raise AgentError("已有写入结果未知，请先核对服务端状态", outcome_unknown=True)
        try:
            return await self.bridge.call(method, **arguments)
        except asyncio.CancelledError:
            self.outcome_unknown |= writing
            raise
        except ToolFailure as error:
            self.outcome_unknown |= writing and error.outcome_unknown
            raise AgentError(
                str(error),
                outcome_unknown=self.outcome_unknown,
            ) from None
        except Exception:  # noqa: BLE001 -- SDK/插件异常不得将远端正文或秘密带到模型。
            self.outcome_unknown |= writing
            raise AgentError(
                "服务操作未完成，请核对连接、授权和实际结果", outcome_unknown=self.outcome_unknown
            ) from None

    async def start(self):
        identity = await self._call("me")
        if (
            not isinstance(identity, dict)
            or not identity.get("id")
            or "access_revision" not in identity
        ):
            raise AgentError("服务未返回有效的员工身份")
        self.identity = identity["id"], identity["access_revision"]

    async def ensure_identity(self):
        current = await self._call("me")
        if (
            self.identity is None
            or (current.get("id"), current.get("access_revision")) != self.identity
        ):
            raise AgentError("当前身份或权限已经变化，请重新开始任务")

    async def read(self, operation: str, arguments: dict):
        spec = READS.get(operation)
        if spec is None:
            raise AgentError("未注册的读取操作")
        arguments = validate_arguments(spec, arguments, self.limits.input_bytes)
        await self.ensure_identity()
        if "limit" in spec.arguments:
            if self.pages >= self.limits.pages:
                raise AgentError("本次任务分页预算已用完，结果可能未全；请缩小范围后重新查询")
            self.pages += 1
            arguments.setdefault("limit", 10)
        result = sanitize(await self._call(spec.method, **arguments))
        encoded(result, self.limits.result_bytes)
        self.receipts.append({"operation": operation, "status": "read", "result": result})
        return result

    async def prepare(self, operation: str, arguments: dict) -> str:
        spec = WRITES.get(operation)
        if spec is None:
            raise AgentError("未注册的写入操作")
        if len(self.proposals) >= self.limits.pending_writes:
            raise AgentError("本次任务待确认意图已达上限")
        args = validate_arguments(spec, arguments, self.limits.input_bytes)
        await self.ensure_identity()
        if operation in {"data", "access", "account"}:
            key = "request" if operation == "account" else "change"
            if operation == "data" and args[key].get("operation") == "project.create":
                args[key].setdefault("payload", {}).setdefault("idempotency_key", str(uuid4()))
            preview = await self._call(
                {
                    "data": "preview_data_change",
                    "access": "preview_access_change",
                    "account": "preview_account",
                }[operation],
                **args,
            )
            digest = preview.get("confirmation_digest")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise AgentError("服务端预览缺少有效摘要")
            args[key] = preview[key]
            args["confirmation_digest"] = digest
            if operation == "account":
                args["idempotency_key"] = str(uuid4())
        elif operation == "account_resume":
            preview = await self._call(
                "preview_account_resume", identifier=args["identifier"], mode=args.get("mode")
            )
            digest = preview.get("confirmation_digest")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise AgentError("恢复预览缺少有效摘要")
            args["confirmation_digest"] = digest
        else:
            before = None
            if operation in {"confirm_record", "correct_record"}:
                before = await self._call("record", record_id=args["record_id"])
            elif operation == "submit_record":
                before = await self._call(
                    "schema", project_id=args["project_id"], revision=args["schema_revision"]
                )
                args["idempotency_key"] = str(uuid4())
            elif operation == "change_authorization":
                # 当前列表中不能证明目标唯一时，不将首屏作全量匹配依据；服务端按ID/版本核验。
                before = {"notice": "请核对目标授权和影响；当前会话可能随操作失效"}
            preview = {"before": before, "after": args, "notice": "候选提交不等于业务确认"}
        ticket = secrets.token_urlsafe(24)
        # 完整参数仅进程内冻结；预览和模型结果仍受各自预算约束。
        encoded(sanitize(preview), self.limits.result_bytes)
        self.proposals[ticket] = Proposal(
            ticket,
            operation,
            encoded(args, self.limits.input_bytes),
            encoded(sanitize(preview), self.limits.result_bytes),
            time.monotonic(),
        )
        return ticket

    def proposal(self, ticket: str) -> Proposal:
        if ticket in self.used:
            raise AgentError("该写入意图已使用，禁止重放")
        if ticket not in self.proposals:
            raise AgentError("未知写入意图")
        proposal = self.proposals[ticket]
        if time.monotonic() - proposal.created_at > self.limits.seconds:
            raise AgentError("预览已过期，请重新预览")
        return proposal

    def deny(self, ticket: str):
        self.proposal(ticket)
        self.used.add(ticket)
        self.receipts.append({"operation": self.proposals[ticket].operation, "status": "denied"})

    async def execute(self, ticket: str):
        proposal = self.proposal(ticket)
        # 批准不替代实时授权；权限改动意味着必须重新预览与人工确认。
        try:
            await self.ensure_identity()
        except AgentError:
            self.used.add(ticket)
            raise
        self.used.add(ticket)  # 先消耗，失败或取消也不自动重放。
        result = await self._call(
            WRITES[proposal.operation].method, writing=True, **proposal.arguments
        )
        result = sanitize(result)
        self.receipts.append(
            {"operation": proposal.operation, "status": "received", "result": result}
        )
        try:
            encoded(result, self.limits.result_bytes)
        except AgentError:
            # 业务已返回成功，但结果未交给模型；不得提示重新写入。
            raise AgentError(
                "服务已返回写入回执，但内容超预算；请用原CLI查询，不要重复提交"
            ) from None
        return result
