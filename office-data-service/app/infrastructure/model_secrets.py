"""专用加密根密钥只在工作线程读取；不复用会话密钥，不回显加密异常。"""

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet

from app.common.errors import DomainError


def cipher(path: Path | None) -> Fernet:
    try:
        if path is None:
            raise ValueError()
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > 128
                or (os.name != "nt" and info.st_mode & 0o077)
            ):
                raise ValueError()
            return Fernet(stream.read(129).strip())
    except (OSError, ValueError):
        raise DomainError(
            503, "execution_unavailable", "模型密钥存储未就绪，请运维配置专用加密根密钥"
        ) from None


def encrypt(path: Path | None, value: str) -> str:
    return cipher(path).encrypt(value.encode()).decode()


def decrypt(path: Path | None, value: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return cipher(path).decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeError, ValueError):
        raise DomainError(
            503, "execution_unavailable", "模型密钥不可解密，请核对原加密根密钥和备份"
        ) from None
