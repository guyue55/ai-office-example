"""从干净已提交模板生成独立企业项目；不复制历史、运行状态或秘密。"""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from configure_template import ROOT, configure, validate

FORBIDDEN = {
    ".git",
    ".venv",
    "__pycache__",
    "runtime",
    "dist",
    "credentials",
    "secrets",
    "runs",
    "logs",
    "storage_data",
}


def generate(source: Path, target: Path, config: Path):
    source, target = source.resolve(), target.absolute()
    validate(config)
    if target.exists() or target.is_symlink():
        raise ValueError("新项目目标必须不存在，禁止覆盖")
    if target.resolve().is_relative_to(source):
        raise ValueError("新项目必须位于模板目录之外")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("目标父目录必须是已存在的普通目录")
    if subprocess.check_output(
        ["git", "diff", "HEAD", "--", ".", ":(exclude)template.toml"], cwd=source
    ):
        raise ValueError("模板存在未提交变更，不能生成来源混杂的项目")
    names = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=source)
        .decode()
        .strip("\0")
        .split("\0")
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    files = []
    for name in names:
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or set(path.parts) & FORBIDDEN
            or any(part.startswith(".env") and part != ".env.example" for part in path.parts)
        ):
            raise ValueError("模板跟踪了不应导出的文件：" + name)
        original = source / path
        if (
            original.is_symlink()
            or not original.is_file()
            or not original.resolve().is_relative_to(source)
        ):
            raise ValueError("拒绝软链或缺失源码：" + name)
        files.append(name)
    temporary = Path(tempfile.mkdtemp(prefix=".office-new-", dir=target.parent))
    try:
        for name in files:
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination)
        shutil.copyfile(config, temporary / "template.toml")
        configure(temporary, temporary / "template.toml")
        (temporary / "template-origin.json").write_text(
            json.dumps(
                {
                    "template_commit": commit,
                    "notice": "只复制受控源码；不包含模板Git历史或实例数据",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"project": str(target), "template_commit": commit, "file_count": len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "template.toml")
    args = parser.parse_args()
    try:
        print(json.dumps(generate(ROOT, args.output, args.config), ensure_ascii=False))
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        parser.exit(2, "生成未完成：" + str(e) + "\n")


if __name__ == "__main__":
    main()
