"""仅测试：凭证通过进程私有stdin进入内存，不落文件/命令参数，不提供生产回退。"""

import json
import runpy
import sys
from pathlib import Path
from urllib.parse import urlsplit

import office_client


def main():
    supplied = json.load(sys.stdin)
    service = supplied["service"]
    credentials = supplied["credentials"]
    original = office_client.OfficeClient
    # 读取本测试副本的配置，允许独立端口；仍拒绝外部服务或其他数据库。
    config = json.loads(
        (Path(__file__).resolve().parents[2] / "runtime/foundation/config.json").read_text()
    )
    expected = config["public_url"]
    parsed = urlsplit(expected)
    if (
        config.get("database_host") != "127.0.0.1"
        or config.get("database_name") != "office"
        or parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("仅允许当前副本的本机独立测试配置")

    class MemoryStore:
        def __init__(self):
            self.value = credentials.copy()

        def load(self):
            return self.value

        def save(self, value):
            self.value = value

        def clear(self):
            self.value = None

    def construct(url):
        if url != service or url != expected:
            raise ValueError("仅允许本机独立测试服务")
        return original(url, store=MemoryStore())

    office_client.OfficeClient = construct
    skill = Path(sys.argv[1])
    sys.argv = ["run.py", "--job", "morning_daily", "--force"]
    sys.path.insert(0, str(skill / "scripts"))
    runpy.run_path(str(skill / "scripts/run.py"), run_name="__main__")


if __name__ == "__main__":
    main()
