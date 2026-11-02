"""每运行独占串行线程；复用同步SDK，不阻塞事件循环或共享认证状态。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Protocol

from office_client.client import ServiceError

from .policy import ToolFailure


class ToolBridge(Protocol):
    """共享运行时的工具端口，员工SDK和内部应用适配均可实现。"""

    async def call(self, method: str, **arguments): ...


class SDKBridge:
    def __init__(self, factory):
        self.factory = factory
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="office-agent-sdk")
        self.client = None
        self.closed = False

    async def __aenter__(self):
        future = asyncio.get_running_loop().run_in_executor(self.executor, self.factory)
        try:
            self.client = await asyncio.shield(future)
        except asyncio.CancelledError:
            try:
                self.client = await future
                await asyncio.get_running_loop().run_in_executor(self.executor, self.client.close)
            finally:
                await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=False)
            raise
        except BaseException:
            await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=False)
            raise
        return self

    async def call(self, method: str, **arguments):
        if self.closed or self.client is None:
            raise RuntimeError("SDK通道已关闭")
        future = asyncio.get_running_loop().run_in_executor(
            self.executor, partial(getattr(self.client, method), **arguments)
        )
        # 外层取消只停止等待，不能假称已经撤回发出的同步HTTP请求。
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # 取消后仍可能有远端错误；收尾时消费异常而不输出其敏感正文。
            future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise
        except ServiceError as error:
            raise ToolFailure(
                f"服务操作未完成（HTTP {error.status}，业务码 {error.code}）；请核对状态",
                outcome_unknown=error.outcome_unknown,
            ) from None

    async def __aexit__(self, *args):
        self.closed = True
        try:
            if self.client is not None:
                await asyncio.get_running_loop().run_in_executor(self.executor, self.client.close)
        finally:
            await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=False)
