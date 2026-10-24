"""统一秘密文件读取；运行期磁盘I/O不得占用事件循环。"""

import asyncio
from pathlib import Path

from app.common.async_work import bounded_thread


def _read_secret(path: Path) -> str:
    if path.stat().st_mode & 0o077:
        raise ValueError("秘密权限不安全")
    value = path.read_text().strip()
    if not value:
        raise ValueError("秘密为空")
    return value


async def read_secret(path: Path, capacity: asyncio.Semaphore) -> str:
    return await bounded_thread(capacity, _read_secret, path)
