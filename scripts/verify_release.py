"""离线复核发布目录、来源、摘要及压缩包边界；仅使用Python标准库。"""

import argparse
import hashlib
import json
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PACKAGES = ("office_api_contracts", "office_data_service", "office_client")
FORBIDDEN = {
    ".git",
    ".venv",
    "__pycache__",
    "runtime",
    "credentials",
    "secrets",
    "storage_data",
    "runs",
    "logs",
    "proposed",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".DS_Store",
    "dist",
    "build",
}


def safe_member(name: str, allow_client_env: bool = False) -> None:
    """不解压未知文件；拒绝路径穿越、秘密及运行产物。"""
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or set(path.parts) & FORBIDDEN
    ):
        raise ValueError("归档包含不允许的路径")
    if (
        path.name.startswith(".env")
        and path.name != ".env.example"
        and not (allow_client_env and name == ".env")
    ):
        raise ValueError("归档包含实例环境文件")
    if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".pyc"}:
        raise ValueError("归档包含秘密或运行产物")


def check_archive(path: Path, client: bool = False) -> None:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or sum(x.file_size for x in infos) > 100_000_000:
                raise ValueError("归档重复或超过预算")
            for info in infos:
                safe_member(info.filename, client)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("归档不接受软链")
            if client:
                expected = (
                    "OFFICE_PUBLIC_URL=https://office.example\nOFFICE_DEPLOYMENT_MODE=https\n"
                )
                if archive.read(".env").decode() != expected:
                    raise ValueError("公开员工包不能携带真实企业地址")
            if path.suffix == ".whl":
                if not any(n.endswith("/licenses/LICENSE") for n in names):
                    raise ValueError("轮包缺少许可证")
                if not any(n.endswith("/licenses/NOTICE") for n in names):
                    raise ValueError("轮包缺少来源声明")
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            total = 0
            for info in archive:
                safe_member(info.name)
                total += info.size
                if not (info.isfile() or info.isdir()) or total > 100_000_000:
                    raise ValueError("源码分发包含特殊文件或超过预算")


def verify(directory: Path, tag: str | None = None, commit: str | None = None) -> dict:
    manifest = json.loads((directory / "release-manifest.json").read_text())
    version = manifest["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or manifest["tag"] != "v" + version:
        raise ValueError("发布版本无效")
    if tag is not None and manifest["tag"] != tag:
        raise ValueError("发布标签不匹配")
    sha = manifest["source_commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or (commit is not None and sha != commit):
        raise ValueError("来源提交不匹配")
    artifacts = manifest["artifacts"]
    source = [n for n in artifacts if n.endswith(f"-source-{version}.zip")]
    client = [n for n in artifacts if n.endswith(f"-client-{version}.zip")]
    if len(source) != 1 or len(client) != 1:
        raise ValueError("源码和员工包不完整")
    allowed = {
        "RELEASE_NOTES.md",
        "build-manifest.json",
        "dependency-versions.json",
        "office-skills.zip",
        "office-skills.zip.sha256",
        source[0],
        client[0],
    }
    for name in PACKAGES:
        allowed.update({f"{name}-{version}-py3-none-any.whl", f"{name}-{version}.tar.gz"})
    if set(artifacts) != allowed:
        raise ValueError("发布资产不符合白名单")
    expected = allowed | {"release-manifest.json", "SHA256SUMS"}
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError("发布目录存在遗漏或额外文件")
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if (
            name in checksums
            or Path(name).name != name
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("摘要清单无效")
        checksums[name] = digest
    if set(checksums) != expected - {"SHA256SUMS"}:
        raise ValueError("摘要清单范围不完整")
    for name, digest in checksums.items():
        path = directory / name
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("资产摘要不匹配")
        if name in artifacts and artifacts[name] != digest:
            raise ValueError("发布清单与摘要不一致")
        check_archive(path, name == client[0])
    build = json.loads((directory / "build-manifest.json").read_text())
    if build["source_commit"] != sha:
        raise ValueError("构建来源不一致")
    return {"tag": manifest["tag"], "source_commit": sha, "assets": len(expected)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--commit")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.directory, args.tag, args.commit), ensure_ascii=False))
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, tarfile.TarError) as error:
        parser.exit(1, "发布核验失败：" + str(error) + "\n")


if __name__ == "__main__":
    main()
