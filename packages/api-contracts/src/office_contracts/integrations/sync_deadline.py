"""同步HTTP调用的有界deadline；超时不重试，名额保留至实际执行结束。"""

import math
from threading import BoundedSemaphore, Event, Thread

from ..errors import TransportFailure

_SLOTS = BoundedSemaphore(8)


def within_deadline(work, timeout: float, *, writing: bool):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("总超时必须为有限正数")
    if not _SLOTS.acquire(blocking=False):
        raise TransportFailure("capacity", "not_applied" if writing else "not_applicable")
    done, cancelled = Event(), Event()
    outcome = []

    def execute():
        try:
            outcome.append((True, work(cancelled)))
        except BaseException as error:  # noqa: BLE001 — 在线程调用方原样重抛，覆盖退出信号。
            outcome.append((False, error))
        finally:
            _SLOTS.release()
            done.set()

    try:
        Thread(target=execute, daemon=True, name="contract-http").start()
    except BaseException:
        _SLOTS.release()
        raise
    if not done.wait(timeout):
        cancelled.set()
        raise TransportFailure("total_timeout", "unknown" if writing else "not_applicable")
    success, value = outcome[0]
    if success:
        return value
    raise value
