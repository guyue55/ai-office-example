"""从管理员提供的完整本地wheel目录安装客户端；不从聊天下载私有程序。"""

import argparse
import os
import subprocess
from pathlib import Path


def install(wheel_dir: Path, tool_root: Path | None = None):
    wheels = []
    for prefix in ("office_client-", "office_api_contracts-"):
        matches = list(wheel_dir.glob(prefix + "*.whl"))
        if len(matches) != 1:
            raise ValueError("安装目录中每个内部包必须恰好有一份wheel，未开始安装")
        wheels.append(matches[0].resolve())
    env = dict(os.environ)
    if tool_root is not None:
        env.update(UV_TOOL_DIR=str(tool_root / "tools"), UV_TOOL_BIN_DIR=str(tool_root / "bin"))
    subprocess.run(
        [
            "uv",
            "tool",
            "install",
            "--no-cache",
            "--reinstall-package",
            "office-client",
            "--reinstall-package",
            "office-api-contracts",
            "--with",
            str(wheels[1]),
            str(wheels[0]),
        ],
        env=env,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument(
        "--tool-root", type=Path, help="隔离验收目录；不指定时使用uv标准用户工具目录"
    )
    args = parser.parse_args()
    install(args.wheel_dir, args.tool_root)


if __name__ == "__main__":
    main()
