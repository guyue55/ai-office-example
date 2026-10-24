"""仅打包干净已跟踪套件；清单校验不等于签名或宿主发现验收。"""

import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"runs", "logs", "storage_data", "proposed", "credentials", "__pycache__", ".git"}
EXTENSIONS = {".md", ".py", ".yaml", ".yml", ".json", ".csv", ".txt", ".gitignore"}


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


def check_config_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                str(key).lower()
                in {"password", "secret", "api_key", "access_token", "refresh_token", "webhook"}
                and item
            ):
                raise ValueError("配置包含不应分发的凭证字段")
            check_config_secrets(item)
    elif isinstance(value, list):
        for item in value:
            check_config_secrets(item)


def package(root: Path, output: Path):
    if (
        output.suffix != ".zip"
        or output.is_symlink()
        or output.resolve().is_relative_to((root / "office-skills").resolve())
    ):
        raise ValueError("候选包须为套件源码之外的zip，不能覆盖源码或软链")
    if git(root, "diff", "HEAD", "--", "office-skills") or git(
        root, "ls-files", "--others", "--exclude-standard", "--", "office-skills"
    ):
        raise ValueError("套件源码未提交，拒绝生成版本混杂的候选包")
    names = git(root, "ls-files", "-z", "--", "office-skills").decode().strip("\0").split("\0")
    files = {}
    for name in sorted(names):
        path, relative = root / name, Path(name)
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("不接受软链或缺失文件")
        if set(relative.parts) & FORBIDDEN or any(
            part.startswith(".env") for part in relative.parts
        ):
            raise ValueError("检测到不应发布的运行文件")
        if path.suffix not in EXTENSIONS and path.name not in {".gitignore", ".gitkeep"}:
            raise ValueError("未登记的打包类型")
        content = path.read_bytes()
        if "config" in relative.parts and path.suffix in {".json", ".yaml", ".yml"}:
            check_config_secrets(yaml.safe_load(content))
        files[name] = content
    if "office-skills/SKILL.md" not in files or "office-skills/RULES.md" not in files:
        raise ValueError("套件入口不完整")
    manifest = {
        "format": 1,
        "source_commit": git(root, "rev-parse", "HEAD").decode().strip(),
        "layout": "whole-suite",
        "host_verified": False,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
        "notice": "仅本地候选制品；SDK/契约库另装，哈希不代表发布签名或宿主兼容。",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".zip", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), content)
            archive.writestr(
                zipfile.ZipInfo("office-skills/package-manifest.json", (1980, 1, 1, 0, 0, 0)),
                json.dumps(manifest, ensure_ascii=False),
            )
        verify_archive(temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n")
    return manifest


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [entry.filename for entry in infos]
        if len(infos) > 1000 or sum(entry.file_size for entry in infos) > 50 * 1024 * 1024:
            raise ValueError("候选包超过验证预算")
        if len(names) != len(set(names)) or any(
            not name.startswith("office-skills/") or ".." in Path(name).parts or "\\" in name
            for name in names
        ):
            raise ValueError("候选包路径或重复条目无效")
        if any((entry.external_attr >> 16) & 0o170000 == 0o120000 for entry in infos):
            raise ValueError("候选包不接受软链条目")
        manifest_name = "office-skills/package-manifest.json"
        manifest = json.loads(archive.read(manifest_name))
        if set(names) != set(manifest["files"]) | {manifest_name}:
            raise ValueError("候选包清单不完整")
        for name, digest in manifest["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("候选包文件摘要不匹配")
    return manifest


def main():
    from check_skills import check

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/office-skills.zip")
    args = parser.parse_args()
    check()
    package(ROOT, args.output)
    manifest = verify_archive(args.output)
    print(
        json.dumps(
            {
                "status": "packaged",
                "files": len(manifest["files"]),
                "source_commit": manifest["source_commit"],
                "host_verified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
