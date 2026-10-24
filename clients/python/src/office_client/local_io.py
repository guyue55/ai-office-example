"""CLI本地输入输出约定；不改变导入SDK的宿主进程。"""

import json
import sys
from pathlib import Path

from .auth import ClientError


def read_json_file(path: str) -> dict:
    """跨平台读取UTF-8对象，兼容Windows编辑器BOM，不回显文件内容。"""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        raise ClientError("输入文件须为有效的UTF-8 JSON对象，请检查文件与编码") from None
    if not isinstance(value, dict):
        raise ClientError("输入文件必须为JSON对象")
    return value


def configure_cli_output():
    """Windows管道也使用UTF-8；仅命令入口调用，不修改SDK使用者的输出。"""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
