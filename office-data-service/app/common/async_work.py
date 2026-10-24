"""受限线程任务：调用方取消不等于线程结束，名额由真实任务释放。"""

import asyncio
from collections.abc import Callable


class WorkCapacityExceeded(Exception):
    pass


async def bounded_thread[T](capacity: asyncio.Semaphore, work: Callable[..., T], *args) -> T:
    if capacity.locked():
        raise WorkCapacityExceeded()
    await capacity.acquire()
    try:
        task = asyncio.create_task(asyncio.to_thread(work, *args))
    except BaseException:
        capacity.release()
        raise

    def completed(result):
        capacity.release()
        if not result.cancelled():
            result.exception()

    task.add_done_callback(completed)
    return await asyncio.shield(task)
