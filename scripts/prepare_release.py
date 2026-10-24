"""从干净已提交源码构建候选制品；不创建tag、发布Release、更新实例或全局工具。"""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path

from check_build import record, verify_artifacts
from configure_template import configure, validate
from package_client_setup import package as package_client
from package_skills import package as package_skills
from verify_release import verify as verify_release

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = (".", "office-data-service", "clients/python", "packages/api-contracts")


def validate_versions(root, tag=None):
    version = (root / "VERSION").read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("VERSION须为稳定版三段数字")
    if tag is not None and tag != "v" + version:
        raise ValueError("标签与VERSION不一致")
    for folder in PROJECTS:
        data = tomllib.loads((root / folder / "pyproject.toml").read_text())
        if data["project"]["version"] != version:
            raise ValueError("工作区版本不一致：" + folder)
    locked = tomllib.loads((root / "uv.lock").read_text())
    versions = {item["name"]: item["version"] for item in locked["package"]}
    for name in (
        "office-foundation",
        "office-data-service",
        "office-client",
        "office-api-contracts",
    ):
        if versions.get(name) != version:
            raise ValueError("锁文件版本不一致：" + name)
    schema = json.loads((root / "contracts/openapi.json").read_text())
    if schema["info"]["version"] != version:
        raise ValueError("OpenAPI版本未同步")
    if not (root / "releases" / ("v" + version + ".md")).is_file():
        raise ValueError("缺少当前版本发布说明")
    return version


def build(root, output, tag):
    configure(root, root / "template.toml", check=True)
    product_id = validate(root / "template.toml")["product"]["id"]
    version = validate_versions(root, tag)
    if output.exists() or output.is_symlink():
        raise ValueError("候选输出已存在，请选择新目录，禁止覆盖既有制品")
    if subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=root):
        raise ValueError("源码未提交，不能声明来源提交")
    # 不忽略未跟踪程序；会议资料可留在工作目录，但不进入归档。
    paths = [
        "scripts",
        "deploy",
        "installers",
        "office-data-service",
        "clients",
        "packages",
        "office-skills",
    ]
    if subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *paths], cwd=root
    ):
        raise ValueError("存在未跟踪程序，请先核对并提交")
    output.mkdir(parents=True)
    for name in ("office-api-contracts", "office-data-service", "office-client"):
        subprocess.run(
            ["uv", "build", "--package", name, "--out-dir", str(output)], cwd=root, check=True
        )
    # uv build会创建本地忽略文件，它不是可下载发布资产。
    (output / ".gitignore").unlink(missing_ok=True)
    manifest = record(root, output)
    verify_artifacts(manifest, output)
    (output / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    package_skills(root, output / "office-skills.zip")
    with tempfile.TemporaryDirectory(prefix="office-release-client-") as temporary:
        client = Path(temporary) / "client"
        # 通用制品不携带企业地址；安装前由AI引导填写交付.env。
        package_client(root, client, "https://office.example", artifact_dir=output)
        with zipfile.ZipFile(
            output / f"{product_id}-client-{version}.zip", "w", zipfile.ZIP_DEFLATED
        ) as archive:
            for path in sorted(client.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(client))
    source = output / f"{product_id}-source-{version}.zip"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            f"--prefix={product_id}-{version}/",
            f"--output={source}",
            "HEAD",
        ],
        cwd=root,
        check=True,
    )
    with zipfile.ZipFile(source) as archive:
        if any(
            set(Path(name).parts) & {"runtime", ".git", "credentials", "__pycache__"}
            for name in archive.namelist()
        ):
            raise ValueError("源码归档包含禁止分发目录")
    (output / "RELEASE_NOTES.md").write_bytes((root / "releases" / (tag + ".md")).read_bytes())
    lock = tomllib.loads((root / "uv.lock").read_text())
    (output / "dependency-versions.json").write_text(
        json.dumps(
            {
                "notice": "依赖锁定清单，不等同SPDX SBOM或许可证审计",
                "packages": [{"name": p["name"], "version": p["version"]} for p in lock["package"]],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.iterdir())
        if p.is_file()
    }
    (output / "release-manifest.json").write_text(
        json.dumps(
            {
                "version": version,
                "tag": tag,
                "source_commit": manifest["source_commit"],
                "artifacts": files,
                "notice": "构建来源记录；实际发布状态以GitHub Release为准",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    entries = sorted(p for p in output.iterdir() if p.is_file())
    (output / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in entries)
    )
    verify_release(output, tag, manifest["source_commit"])
    print("RELEASE_CANDIDATE_OK " + str(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="v1.0.0")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.check:
        print("RELEASE_VERSION_OK " + validate_versions(ROOT, args.tag))
    else:
        build(ROOT, (args.output or ROOT / "dist" / args.tag).resolve(), args.tag)


if __name__ == "__main__":
    main()
