"""运行与离线首管理员引导；不提供公开 bootstrap 接口。"""

import argparse
import json
from pathlib import Path

import uvicorn

from app.common.metadata import SERVICE_TITLE
from app.infrastructure.config import Settings
from app.infrastructure.database import sessions
from app.modules.access.service import bootstrap


def main():
    parser = argparse.ArgumentParser(description=SERVICE_TITLE)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1", help="容器内显式设置0.0.0.0")
    serve.add_argument("--port", type=int, default=8800)
    serve.add_argument("--proxy-trusted-ips", default="", help="仅信任明确配置的反向代理IP/CIDR")
    boot = sub.add_parser("bootstrap")
    boot.add_argument("--identity-file", required=True)
    args = parser.parse_args()
    if args.command == "serve" and not 1 <= args.port <= 65535:
        parser.error("端口必须为1–65535")
    settings = Settings.load()
    if args.command == "serve":
        uvicorn.run(
            "app.main:create_app",
            factory=True,
            # 内存Web会话只支持单进程，不能被WEB_CONCURRENCY隐式扩容。
            workers=1,
            host=args.host,
            port=args.port,
            access_log=False,
            proxy_headers=bool(args.proxy_trusted_ips),
            forwarded_allow_ips=args.proxy_trusted_ips,
        )
    else:
        if settings.database_user != "office_owner":
            parser.error("离线引导必须使用 owner 配置")
        identity = json.loads(Path(args.identity_file).read_text())
        with sessions(settings).begin() as session:
            identifier = bootstrap(session, settings.issuer, identity["subject"], identity["name"])
        print(json.dumps({"employee_id": identifier, "status": "initialized"}))
