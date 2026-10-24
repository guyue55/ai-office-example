"""隔离安装候选轮包并检验CLI入口，不升级全局工具、不读取原有授权。"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from check_build import verify_artifacts
from install_client import install
from office_client.product import PRODUCT


def check(wheel_dir: Path):
    manifest_path = wheel_dir / "build-manifest.json"
    manifest = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verify_artifacts(manifest, wheel_dir)
    with tempfile.TemporaryDirectory(prefix="office-client-check-") as temporary:
        root = Path(temporary)
        install(wheel_dir, root)
        executable = (
            root
            / "bin"
            / (PRODUCT["command"] + ".exe" if sys.platform == "win32" else PRODUCT["command"])
        )
        payload = root / "中文 输入.json"
        payload.write_text(
            json.dumps(
                {"project_id": "平台验收", "values": {"说明": "构造输入"}}, ensure_ascii=False
            ),
            encoding="utf-8-sig",
        )
        result = subprocess.run(
            [
                str(executable),
                "--service",
                "https://office.example",
                "preview",
                "--file",
                str(payload),
            ],
            check=True,
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        preview = json.loads(result.stdout)
        if preview["project_id"] != "平台验收" or preview["field_ids"] != ["说明"]:
            raise RuntimeError("候选轮包中文输入输出不一致")
        help_result = subprocess.run(
            [str(executable)],
            check=True,
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if "常用示例" not in help_result.stdout or help_result.stderr:
            raise RuntimeError("候选轮包无参数帮助入口异常")
        subprocess.run([str(executable), "--version"], check=True)
        diagnosis = subprocess.run(
            [str(executable), "doctor"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        actual = json.loads(diagnosis.stdout)["content_identity"]
        if manifest is not None:
            expected = {item["name"]: item["content_sha256"] for item in manifest["artifacts"]}
            if any(actual[name] != expected[name] for name in actual):
                raise RuntimeError("候选安装内容与构建清单不一致")
    print("CLIENT_CANDIDATE_INSTALL_OK preview_offline=true credentials_accessed=false")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel-dir", type=Path, default=Path(__file__).resolve().parents[1] / "dist"
    )
    check(parser.parse_args().wheel_dir)
