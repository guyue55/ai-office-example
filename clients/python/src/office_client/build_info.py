"""离线内容身份；只读取已安装模块资源，不读取凭证、用户配置或运行数据。"""

import hashlib
import importlib.util
import json
from pathlib import Path

PACKAGES = {
    "office-client": "office_client",
    "office-api-contracts": "office_contracts",
    "office-data-service": "app",
}


def content_identity(contents):
    """轮包与安装目录使用同一种规范化指纹；不是发布签名。"""
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in contents}
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def installed_identity(distribution):
    module = PACKAGES[distribution]
    spec = importlib.util.find_spec(module)
    if spec is None or not spec.submodule_search_locations:
        return None
    root = Path(next(iter(spec.submodule_search_locations)))
    contents = []
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        ):
            if path.is_symlink():
                raise ValueError("模块资源不接受软链")
            contents.append((module + "/" + path.relative_to(root).as_posix(), path.read_bytes()))
    return content_identity(contents)
