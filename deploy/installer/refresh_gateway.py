"""按实例公开配置刷新网关；不改身份、数据库、端口或令牌。"""

import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "office_layout", Path(__file__).with_name("layout.py")
)
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    args = parser.parse_args()
    root = args.instance.resolve(strict=True)
    marker = json.loads((root / "installation.json").read_text(encoding="utf-8"))
    target = root / "gateway/nginx.conf"
    content = layout.nginx(marker["settings"])
    if target.read_text(encoding="utf-8") == content:
        print("unchanged")
        return
    stat = target.stat()
    layout.write(target, content, stat.st_uid, stat.st_gid)
    print("updated")


if __name__ == "__main__":
    main()
