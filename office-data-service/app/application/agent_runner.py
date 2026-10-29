"""可选内部Agent任务执行；只有一套共享模型循环，不重放重启遗留任务。"""

import asyncio
from datetime import UTC, datetime
from importlib import import_module

from sqlalchemy import select

from app.application.agent_history import ConversationContext
from app.application.agent_progress import ProgressWriter
from app.application.agent_tools import ApplicationToolBridge
from app.common.security_context import agent_run_context
from app.infrastructure.models import AgentRun, Audit
from app.modules.agent.service import ACTIVE


async def recover_runs(sessions):
    """单工作进程启动恢复；保留结果，绝不恢复执行或重放旧审批。"""
    async with sessions.begin() as db:
        rows = list(
            await db.scalars(select(AgentRun).where(AgentRun.status.in_(ACTIVE)).with_for_update())
        )
        for row in rows:
            row.status = "outcome_unknown" if row.write_inflight else "interrupted"
            row.pending = None
            row.version += 1
            row.updated_at = datetime.now(UTC)


class AgentRunner:
    def __init__(self, application, config, *, model_context=None):
        self.application, self.config = application, config
        self.sessions = application.sessions
        self.core = import_module("office_agent.runtime")
        self.gateway_type = import_module("office_agent.gateway").Gateway
        self.model_context = (
            model_context or import_module("office_agent.providers").configured_model
        )
        self.sanitize = import_module("office_agent.policy").sanitize
        writes = import_module("office_agent.catalog").WRITES
        self.write_methods = {item.method for item in writes.values()}
        self.tasks: dict[str, asyncio.Task] = {}
        self.closed = False

    async def recover(self):
        # 官方启动入口固定workers=1；不以这个实现宣称支持多个独立执行器。
        await recover_runs(self.sessions)

    def launch(self, identifier, authenticate):
        if self.closed or identifier in self.tasks:
            return
        task = asyncio.create_task(
            self.run(identifier, authenticate), name="office-agent-" + identifier
        )
        self.tasks[identifier] = task

        def done(completed):
            self.tasks.pop(identifier, None)
            if not completed.cancelled():
                completed.exception()  # 错误状态由持久化记录说明，不输出第三方异常正文。

        task.add_done_callback(done)

    async def update(self, identifier, **values):
        async with self.sessions.begin() as db:
            row = await db.get(AgentRun, identifier, with_for_update=True)
            if row is None:
                raise RuntimeError("任务记录已缺失")
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
            row.updated_at = datetime.now(UTC)

    async def snapshot(self, identifier):
        async with self.sessions() as db:
            row = await db.get(AgentRun, identifier)
            if row is None:
                raise RuntimeError("任务记录已缺失")
            return row

    async def approve(self, identifier, proposal):
        await self.update(
            identifier,
            status="awaiting_confirmation",
            pending={
                "ticket": proposal.ticket,
                "operation": proposal.operation,
                "arguments": self.sanitize(proposal.arguments),
                "preview": proposal.preview,
            },
        )
        # 有界于共享运行时的任务总期限，不无限后台等待。
        while True:
            await asyncio.sleep(0.2)
            row = await self.snapshot(identifier)
            if row.cancel_requested or row.status not in ACTIVE:
                raise asyncio.CancelledError
            if (
                row.pending
                and row.pending.get("ticket") == proposal.ticket
                and "decision" in row.pending
            ):
                decision = row.pending["decision"] is True
                await self.update(identifier, status="running", pending=None)
                return decision

    async def observe(self, identifier, method, phase, result):
        if method not in self.write_methods:
            return
        row = await self.snapshot(identifier)
        if phase == "before":
            if row.cancel_requested or row.status not in ACTIVE:
                raise asyncio.CancelledError
            await self.update(identifier, write_inflight=True)
        elif phase == "rejected":
            await self.update(identifier, write_inflight=False)
        else:
            previous = row.result or {"receipts": []}
            previous = {
                **previous,
                "receipts": [
                    *previous.get("receipts", []),
                    {"operation": method, "status": "received", "result": self.sanitize(result)},
                ],
            }
            # 先保存实际回执再解除在途标记；崩溃窗口保守视为未知，不重放。
            await self.update(identifier, write_inflight=False, result=previous)

    async def finish(self, identifier, status, result=None):
        async with self.sessions.begin() as db:
            row = await db.get(AgentRun, identifier, with_for_update=True)
            if row is None:
                return
            if row.write_inflight:
                status = "outcome_unknown"
            elif row.cancel_requested:
                status = "cancelled"
            row.status, row.pending = status, None
            if result is not None:
                if "receipts" not in result and row.result is not None:
                    result = {**row.result, **result}
                row.result = result
            row.version += 1
            row.updated_at = datetime.now(UTC)
            db.add(
                Audit(
                    actor_id=row.owner_id,
                    action="agent.finished",
                    resource_id=row.id,
                    detail={"status": status},
                    trace_id=row.trace_id,
                )
            )

    async def run(self, identifier, authenticate):
        context = agent_run_context.set(identifier)
        try:
            row = await self.snapshot(identifier)
            if row.cancel_requested:
                await self.finish(identifier, "cancelled")
                return
            await self.update(identifier, status="running")
            history = ConversationContext(self.application, row, authenticate)
            prompt = await history.prompt(self.config.limits.input_bytes)
            bridge = ApplicationToolBridge(
                self.application,
                history.principal,
                observer=lambda m, p, r: self.observe(identifier, m, p, r),
            )
            gateway = self.gateway_type(bridge, self.config.limits)
            async with self.model_context(self.config) as model:
                result = await self.core.run_with_gateway(
                    model,
                    gateway,
                    prompt,
                    approve=lambda proposal: self.approve(identifier, proposal),
                    observe=ProgressWriter(self.sessions, identifier),
                    stream_output=getattr(self.config, "stream_output", True),
                )
            status = "completed" if result.status == "completed" else "failed"
            await self.finish(identifier, status, result.as_dict())
        except asyncio.CancelledError:
            await self.finish(identifier, "interrupted")
            raise
        except Exception:  # noqa: BLE001 -- 未知异常不外泄模型、凭证或业务正文。
            await self.finish(
                identifier, "failed", {"explanation": "任务未完成，请核对配置与回执；不会自动重试"}
            )
        finally:
            agent_run_context.reset(context)

    def cancel(self, identifier):
        if task := self.tasks.get(identifier):
            task.cancel()

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
