"""从真实路由与注册表导出契约；不读取实例配置、凭证或连接数据库。"""

import argparse
import json
import os
import tempfile
from pathlib import Path

from app.application.codebook import build_registry
from app.entrypoints.http.schema import public_schema

ROOT = Path(__file__).resolve().parents[1]


def artifacts():
    """先构造全部内容；生成失败时不修改任何已发布文件。"""
    encoded = json.dumps(build_registry().export(), ensure_ascii=False, indent=2) + "\n"
    return {
        "contracts/business-codes.json": encoded,
        "clients/python/src/office_client/business_codes.json": encoded,
        "contracts/openapi.json": json.dumps(public_schema(), ensure_ascii=False, indent=2) + "\n",
    }


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".contract-", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish(root: Path, contents: dict[str, str]):
    """每文件原子替换；写入异常时恢复本进程已替换的文件。中断后的漂移由check阻断。"""
    old = {
        name: (root / name).read_bytes() if (root / name).exists() else None for name in contents
    }
    changed = []
    try:
        for name, content in contents.items():
            atomic_write(root / name, content.encode())
            changed.append(name)
    except Exception:
        for name in reversed(changed):
            previous = old[name]
            if previous is None:
                (root / name).unlink(missing_ok=True)
            else:
                atomic_write(root / name, previous)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="只读核对；缺失或漂移时失败，不更新文件"
    )
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    contents = artifacts()
    if args.check:
        mismatches = [
            name
            for name, text in contents.items()
            if not (args.output_root / name).is_file()
            or (args.output_root / name).read_bytes() != text.encode()
        ]
        if mismatches:
            parser.exit(1, "契约缺失或漂移，请重新导出并审核：" + ", ".join(mismatches) + "\n")
        print("CONTRACT_SNAPSHOTS_OK")
    else:
        publish(args.output_root, contents)
        print("CONTRACT_SNAPSHOTS_EXPORTED")


if __name__ == "__main__":
    main()
