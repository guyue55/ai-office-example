"""运行时的有界展示投影；不转发原始事件、思维、参数或凭证。"""

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable

from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.models.function import FunctionModel

from .catalog import READS, WRITES
from .policy import sanitize

Observer = Callable[[dict], Awaitable[None]]


class Progress:
    def __init__(self, observer: Observer | None, *, text_limit=16000):
        self.observer = observer
        self.started = time.monotonic()
        self.seq = 0
        self.text_limit = min(text_limit, 16000)
        self.text = ""
        self.steps: list[dict] = []
        self.truncated = False
        self.pending: dict | None = None
        self.worker: asyncio.Task | None = None

    async def emit(self, stage):
        if self.observer is None:
            return
        self.seq += 1
        safe = sanitize(self.text)
        if len(safe.encode()) > self.text_limit:
            safe = safe.encode()[: self.text_limit].decode(errors="ignore")
            self.truncated = True
        payload = {
            "schema_version": 1,
            "seq": self.seq,
            "elapsed_ms": int((time.monotonic() - self.started) * 1000),
            "stage": stage,
            "text": safe,
            "steps": copy.deepcopy(self.steps),
            "truncated": self.truncated,
        }
        # 模型事件必须及时消费；最新快照替换待保存槽，绝不排无限队列。
        self.pending = payload
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._drain(), name="office-agent-progress")

    async def _drain(self):
        while self.pending is not None and self.observer is not None:
            payload, self.pending = self.pending, None
            try:
                async with asyncio.timeout(1):
                    await self.observer(payload)
            except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- 观察失败与业务隔离。
                # 仅结束观察任务；不取消模型、不重放业务、不输出异常正文。
                self.observer = None
                self.pending = None
                return

    async def flush(self):
        """业务终态前收尾最新快照；单观察者串行，不共享并发数据库会话。"""
        if self.worker is not None:
            try:
                await asyncio.shield(self.worker)
            except asyncio.CancelledError:
                await self.cancel()
                raise

    async def cancel(self):
        self.pending = None
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    async def stream(self, _context, events):
        await self.emit("model")
        if self.text and not self.truncated:
            self.text += "\n\n"
        async for event in events:
            text = ""
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                text = event.part.content
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                text = event.delta.content_delta
            # ThinkingPart、签名和所有供应商元信息明确不进入展示投影。
            if text and not self.truncated:
                candidate = self.text + text
                if len(candidate.encode()) > self.text_limit:
                    self.truncated = True
                else:
                    self.text = candidate
                await self.emit("reply")

    async def call(self, operation, execute):
        # 非法模型参数仍交网关拒绝；展示层只记录注册名称，不回显任意输入。
        known = operation in READS or operation in WRITES
        if not known and not (operation.startswith("prepare:") and operation[8:] in WRITES):
            operation = "unregistered"
        step = None
        if len(self.steps) < 64:
            step = {
                "id": str(len(self.steps) + 1),
                "operation": operation,
                "status": "running",
                "elapsed_ms": 0,
            }
            self.steps.append(step)
        else:
            self.truncated = True
        start = time.monotonic()
        await self.emit("tool")
        try:
            result = await execute()
        except BaseException:
            if step is not None:
                step.update(status="failed", elapsed_ms=int((time.monotonic() - start) * 1000))
            await self.emit("tool")
            raise
        if step is not None:
            step.update(status="completed", elapsed_ms=int((time.monotonic() - start) * 1000))
        await self.emit("model")
        return result


def stream_handler(model, progress, enabled=True):
    """显式非流式配置与无stream函数的测试模型保持原run语义，不错误后自动重试。"""
    if not enabled or isinstance(model, FunctionModel) and model.stream_function is None:
        return None
    return progress.stream
