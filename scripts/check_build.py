"""为干净源码的候选轮包记录来源，或在安装环境离线核对模块内容身份。"""

import argparse
import hashlib
import json
import subprocess
import zipfile
from email.parser import BytesParser
from pathlib import Path

from office_client.build_info import PACKAGES, content_identity, installed_identity

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "office-client": "clients/python/src/office_client",
    "office-api-contracts": "packages/api-contracts/src/office_contracts",
    "office-data-service": "office-data-service/app",
}


def record(root, directory):
    if subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=root):
        raise ValueError("源码有未提交变更，拒绝标记来源提交")
    # 不把未跟踪源码混入来源声明；客户纪要等无关文档不参与包构建。
    paths = ["clients/python", "office-data-service", "packages/api-contracts", "scripts"]
    if subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *paths], cwd=root
    ):
        raise ValueError("存在未跟踪程序，拒绝标记来源提交")
    artifacts = []
    for path in sorted(directory.glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            metadata_names = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise ValueError("轮包元数据无效")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
            name = metadata["Name"]
            if name not in PACKAGES:
                continue
            module = PACKAGES[name]
            digest = content_identity(
                (entry, archive.read(entry))
                for entry in archive.namelist()
                if entry.startswith(module + "/") and not entry.endswith("/")
            )
            source = root / SOURCES[name]
            actual = content_identity(
                (module + "/" + entry.relative_to(source).as_posix(), entry.read_bytes())
                for entry in source.rglob("*")
                if entry.is_file()
                and "__pycache__" not in entry.parts
                and entry.suffix not in {".pyc", ".pyo"}
            )
            if digest != actual:
                raise ValueError("轮包与当前源码不一致，请重新构建：" + name)
            artifacts.append(
                {
                    "name": name,
                    "version": metadata["Version"],
                    "file": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "content_sha256": digest,
                }
            )
    if len(artifacts) != len(PACKAGES) or {item["name"] for item in artifacts} != set(PACKAGES):
        raise ValueError("需要每个工作区包各一个候选轮包，不能混入历史版本")
    return {
        "format": 1,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "contract_sha256": {
            name: hashlib.sha256((root / "contracts" / name).read_bytes()).hexdigest()
            for name in ("openapi.json", "business-codes.json")
        },
        "artifacts": artifacts,
        "notice": "内容与来源核对，不代表签名、上线或生产兼容验收",
    }


def verify_artifacts(manifest, directory):
    """只读取清单同目录的轮包；拒绝越界路径，发现任何篡改即失败。"""
    for item in manifest["artifacts"]:
        name = item["file"]
        if Path(name).name != name or "/" in name or "\\" in name or not name.endswith(".whl"):
            raise ValueError("制品文件名无效")
        path = directory / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("轮包哈希不匹配")


def verify_installed(manifest, names):
    indexed = {item["name"]: item for item in manifest["artifacts"]}
    result = {}
    for name in names:
        if name not in PACKAGES or name not in indexed:
            raise ValueError("未登记的模块")
        result[name] = installed_identity(name) == indexed[name]["content_sha256"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--manifest", type=Path, default=ROOT / "dist/build-manifest.json")
    parser.add_argument("--installed", nargs="+", choices=list(PACKAGES))
    args = parser.parse_args()
    if args.installed:
        result = verify_installed(
            json.loads(args.manifest.read_text(encoding="utf-8")), args.installed
        )
        print(json.dumps(result, ensure_ascii=False))
        if not all(result.values()):
            raise SystemExit(1)
    else:
        manifest = record(ROOT, args.wheel_dir)
        args.manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("BUILD_MANIFEST_OK")


if __name__ == "__main__":
    main()
