"""将已核验轮包与完整技能装成面向员工的交付目录，不携带运行凭证。"""

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from check_build import verify_artifacts
from delivery_config import read_env
from office_client.auth import trusted_url
from package_skills import verify_archive

ROOT = Path(__file__).resolve().parents[1]


def package(root, output, service, *, allow_lan_http_demo=False, artifact_dir=None):
    artifacts = artifact_dir if artifact_dir is not None else root / "dist"
    trusted_url(service, loopback_http=False, allow_lan_http_demo=allow_lan_http_demo)
    url = urlsplit(service)
    if (
        (url.scheme != "https" and not allow_lan_http_demo)
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise ValueError("必须提供企业HTTPS根地址")
    if output.exists() or output.is_symlink():
        raise ValueError("输出目录已存在，请选择新的交付目录")
    manifest = json.loads((artifacts / "build-manifest.json").read_text(encoding="utf-8"))
    verify_artifacts(manifest, artifacts)
    suite = verify_archive(artifacts / "office-skills.zip")
    if suite["source_commit"] != manifest["source_commit"]:
        raise ValueError("技能与轮包来源提交不同，须重新构建")
    requirements = subprocess.check_output(
        [
            "uv",
            "export",
            "--locked",
            "--package",
            "office-client",
            "--no-dev",
            "--no-emit-workspace",
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
    )
    output.mkdir(parents=True)
    try:
        with zipfile.ZipFile(artifacts / "office-skills.zip") as archive:
            archive.extractall(output)  # verify_archive已拒绝越界及软链。
        (output / "wheels").mkdir()
        for item in manifest["artifacts"]:
            if item["name"] in ("office-client", "office-api-contracts"):
                shutil.copyfile(artifacts / item["file"], output / "wheels" / item["file"])
        for path in (root / "installers/client").iterdir():
            if path.is_file() and path.suffix in (".py", ".ps1", ".sh", ".md", ".json"):
                if path.suffix == ".ps1":
                    # Windows PowerShell 5.1需要BOM正确识别中文；源文件仍保持UTF-8。
                    (output / path.name).write_text(
                        path.read_text(encoding="utf-8"), encoding="utf-8-sig"
                    )
                else:
                    shutil.copyfile(path, output / path.name)
        shutil.copyfile(root / "scripts/delivery_config.py", output / "delivery_config.py")
        (output / ".env").write_text(
            "OFFICE_PUBLIC_URL="
            + service.rstrip("/")
            + "\nOFFICE_DEPLOYMENT_MODE="
            + ("lan-http-demo" if allow_lan_http_demo else "https")
            + "\n",
            encoding="utf-8",
        )
        (output / "requirements.txt").write_text(requirements, encoding="utf-8")
        files = {
            path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in output.rglob("*")
            if path.is_file() and path.name != ".env"
        }
        (output / "bundle.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "connection_config": ".env",
                    "service": service.rstrip("/"),
                    "source_commit": manifest["source_commit"],
                    "files": files,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, zipfile.BadZipFile):
        shutil.rmtree(output)
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service")
    parser.add_argument("--artifact-dir", type=Path, help="候选轮包和构建清单目录")
    parser.add_argument("--config", type=Path, help="读取服务端同一份交付.env")
    parser.add_argument("--lan-http-demo", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    service, demo = args.service, args.lan_http_demo
    if args.config:
        values = read_env(args.config)
        if service and service != values["OFFICE_PUBLIC_URL"]:
            parser.error("地址与交付配置冲突")
        service, demo = (
            values["OFFICE_PUBLIC_URL"],
            values["OFFICE_DEPLOYMENT_MODE"] == "lan-http-demo",
        )
    if not service:
        parser.error("请提供--config或--service")
    print(
        package(
            ROOT,
            args.output.resolve(),
            service,
            allow_lan_http_demo=demo,
            artifact_dir=args.artifact_dir.resolve() if args.artifact_dir else None,
        )
    )


if __name__ == "__main__":
    main()
