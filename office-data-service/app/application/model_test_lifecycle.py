"""单执行进程的测试收尾与恢复；不重新请求供应商。"""

import asyncio
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.models import Audit, ModelConnectionTest

logger = logging.getLogger("office.service")


class ModelTestLifecycle:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], timeout: float) -> None:
        self.sessions = sessions
        self.timeout = min(5, timeout)
        self.guard = asyncio.Lock()
        self.active: set[str] = set()
        self.tasks: dict[str, asyncio.Task[bool]] = {}

    async def finish(
        self, identifier: str, actor_id: str, revision: int, trace_id: str, result: dict | None
    ) -> bool:
        async def persist() -> bool:
            try:
                async with asyncio.timeout(self.timeout), self.sessions.begin() as db:
                    row = await db.get(ModelConnectionTest, identifier, with_for_update=True)
                    if row is None and result is not None:
                        raise RuntimeError("已执行测试的记录缺失")
                    if row is not None:
                        row.state = "completed" if result is not None else "interrupted"
                        row.result = result
                        detail = {"phase": row.state}
                        if result is not None:
                            detail.update(passed=result["passed"], category=result["category"])
                        db.add(
                            Audit(
                                actor_id=actor_id,
                                action="agent.config.tested",
                                resource_id=str(revision),
                                detail=detail,
                                trace_id=trace_id,
                            )
                        )
                return True
            except Exception:  # noqa: BLE001 -- DB／驱动异常可能含SQL，不记录原文。
                logger.warning(
                    "[Trace: OfficeService/model_test/finalize_failed] trace_id=%s", trace_id
                )
                return False
            finally:
                self.active.discard(identifier)

        task = asyncio.create_task(persist())
        self.tasks[identifier] = task
        task.add_done_callback(lambda done: self.tasks.pop(identifier, None))
        # 重复取消只取消等待者，任务仍由本组件持有并在关闭时等待。
        return await asyncio.shield(task)

    async def recover(self) -> None:
        # 登记＋开始事务提交也持有guard，不能用旧active快照误判新请求。
        async with self.guard, asyncio.timeout(self.timeout), self.sessions.begin() as db:
            rows = await db.scalars(
                select(ModelConnectionTest)
                .where(
                    ModelConnectionTest.state == "started",
                    ModelConnectionTest.id.not_in(self.active),
                )
                .order_by(ModelConnectionTest.created_at.desc(), ModelConnectionTest.id.desc())
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            for row in rows:
                row.state = "interrupted"
                db.add(
                    Audit(
                        actor_id=row.actor_id,
                        action="agent.config.tested",
                        resource_id=str(row.revision),
                        detail={"phase": "interrupted", "reason": "recovered_without_retry"},
                        trace_id=str(uuid4()),
                    )
                )

    async def close(self) -> None:
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
