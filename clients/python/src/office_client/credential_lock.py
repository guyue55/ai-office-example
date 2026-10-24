"""本机凭证协调；锁文件只含占位字节，不保存任何凭证或账号信息。"""

import errno
import hashlib
import os
import stat
import sys
import threading
import time
import weakref
from contextlib import contextmanager
from pathlib import Path


class CredentialLockError(Exception):
    pass


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.depth = 0


_registry: weakref.WeakValueDictionary[str, _State] = weakref.WeakValueDictionary()
_registry_guard = threading.Lock()


def lock_directory():
    return Path.home() / ".cache" / "office" / "credential-locks"


@contextmanager
def _file_lock(key, timeout):
    directory = lock_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or (sys.platform != "win32" and directory.stat().st_mode & 0o077):
        raise CredentialLockError("凭证锁目录权限不安全，请在本机修复")
    path = directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (
            sys.platform != "win32" and (info.st_mode & 0o077 or info.st_uid != os.getuid())
        ):
            raise CredentialLockError("凭证锁文件权限不安全，请在本机修复")
        if info.st_size == 0:
            os.write(fd, b"0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise CredentialLockError("其他进程正在处理登录，请稍后重试") from None
                time.sleep(0.025)
        yield
    finally:
        try:
            if acquired:
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    # 不删除锁文件，避免等待进程仍持旧inode而新进程锁到新文件。


@contextmanager
def credential_guard(store, timeout=15):
    external = getattr(store, "coordination_key", None)
    key = external if isinstance(external, str) else f"memory:{id(store)}"
    with _registry_guard:
        state = _registry.get(key)
        if state is None:
            state = _State()
            _registry[key] = state
    if not state.lock.acquire(timeout=timeout):
        raise CredentialLockError("当前凭证正在处理，请稍后重试")
    try:
        if state.depth or not external:
            state.depth += 1
            try:
                yield
            finally:
                state.depth -= 1
        else:
            with _file_lock(key, timeout):
                state.depth += 1
                try:
                    yield
                finally:
                    state.depth -= 1
    finally:
        state.lock.release()
