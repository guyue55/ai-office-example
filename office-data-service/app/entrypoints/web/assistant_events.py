"""同源只读SSE：仅投影已有任务快照，重连不恢复执行；持续核验身份。"""

import asyncio
import time
from collections import Counter

from app.common.errors import DomainError
from app.modules.agent.state import ACTIVE


class Subscriptions:
    def __init__(self):
        self.runs: Counter[str] = Counter()

    def acquire(self, identifier):
        if self.runs[identifier] >= 3 or self.runs.total() >= 32:
            raise DomainError(429, "agent_capacity", "会话订阅较多，请关闭重复页面后重连")
        self.runs[identifier] += 1

        released = False

        def release():
            nonlocal released
            if released:
                return
            released = True
            self.runs[identifier] -= 1
            if self.runs[identifier] <= 0:
                del self.runs[identifier]

        return release


async def snapshots(request, initial, load, encode, *, interval=0.5, seconds=30):
    """完整快照自带版本及进度序号；事件缺口通过最新快照恢复，无增量重放依赖。"""
    deadline, previous = time.monotonic() + seconds, None
    value = initial
    while True:
        if await request.is_disconnected():
            return
        body = encode(value)
        if body != previous:
            previous = body
            yield b"event: snapshot\ndata: " + body + b"\n\n"
        else:
            yield b": heartbeat\n\n"
        if value.get("history_hidden") or value["status"] not in ACTIVE:
            yield b"event: end\ndata: {}\n\n"
            return
        if time.monotonic() >= deadline:
            yield b"event: resync\ndata: {}\n\n"
            return
        await asyncio.sleep(interval)
        try:
            # 每次独立请求身份／权限，不能持有旧Principal或长数据库事务。
            value = await load()
        except DomainError as error:
            event = "access_lost" if error.status in {401, 403, 404} else "resync"
            yield f"event: {event}\ndata: {{}}\n\n".encode()
            return
