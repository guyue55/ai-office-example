"""原子更新实例镜像记录；只改Compose与安装元数据，不操作数据库或容器。"""

import argparse
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "office_layout", Path(__file__).with_name("layout.py")
)
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--status", choices=["migrating", "healthy", "failed"], required=True)
    parser.add_argument("--from-image", default="")
    parser.add_argument("--backup", default="")
    parser.add_argument("--migration", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args()
    root = args.instance.resolve(strict=True)
    if args.instance.is_symlink() or not root.is_dir():
        raise ValueError("实例目录无效")
    marker_path = root / "installation.json"
    compose_path = root / "compose.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    compose = json.loads(compose_path.read_text(encoding="utf-8"))
    services = compose.get("services", {})
    if not {"service", "setup"} <= services.keys():
        raise ValueError("Compose缺少service或setup")
    old = services["service"].get("image", "")
    services["service"]["image"] = args.image
    services["setup"]["image"] = args.image
    marker["image"] = args.image
    marker["last_upgrade"] = {
        "at": datetime.now(UTC).isoformat(),
        "commit": args.commit,
        "from_image": args.from_image or old,
        "image": args.image,
        "migration": args.migration,
        "backup": args.backup,
        "status": args.status,
    }
    for path, value in ((compose_path, compose), (marker_path, marker)):
        stat = path.stat()
        layout.write(path, value, stat.st_uid, stat.st_gid)
        path.chmod(stat.st_mode & 0o777)
    print(json.dumps({"old_image": old, "image": args.image, "status": args.status}))


if __name__ == "__main__":
    main()
