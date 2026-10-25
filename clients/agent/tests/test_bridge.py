"""验证线程归属和取消收尾，不用sleep模拟成功的网络结果。"""

import asyncio
import threading
import time

from office_agent.bridge import SDKBridge


def test_sdk_lifetime_is_one_worker_and_loop_keeps_progress():
    ids = []

    class Blocking:
        def __init__(self):
            ids.append(threading.get_ident())

        def slow(self):
            ids.append(threading.get_ident())
            time.sleep(0.1)
            return "ok"

        def close(self):
            ids.append(threading.get_ident())

    async def scenario():
        async with SDKBridge(Blocking) as bridge:
            job = asyncio.create_task(bridge.call("slow"))
            await asyncio.sleep(0.01)
            assert not job.done()
            assert await job == "ok"
        assert len(ids) == 3 and len(set(ids)) == 1
        assert ids[0] != threading.get_ident()

    asyncio.run(scenario())


def test_cancel_during_construction_closes_created_client():
    created, closed = threading.Event(), threading.Event()

    class Slow:
        def __init__(self):
            created.set()
            time.sleep(0.05)

        def close(self):
            closed.set()

    async def scenario():
        async def task_body():
            async with SDKBridge(Slow):
                raise AssertionError("不应进入任务体")

        task = asyncio.create_task(task_body())
        while not created.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert closed.is_set()

    asyncio.run(scenario())
