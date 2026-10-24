"""容器内安装阶段；日志只输出阶段及错误类别，不回显秘密或上游响应。"""

import argparse
import json
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from product import PRODUCT

sys.path.insert(0, "/helpers")
from delivery_config import read_env, server_settings
from identity import configure
from layout import prepare, settings, write

ROOT = Path("/state")


def wait_identity(http, deadline=600):
    until = time.monotonic() + deadline
    while time.monotonic() < until:
        try:
            if (
                http.get(
                    "http://identity:8080/identity/realms/master/.well-known/openid-configuration"
                ).status_code
                == 200
            ):
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("身份服务初始化超时；实例已保留，可用相同命令续跑")


def identity_phase():
    if (ROOT / "identity.done").exists():
        return
    marker = json.loads((ROOT / "installation.json").read_text())
    with httpx.Client(timeout=90, trust_env=False, follow_redirects=False) as http:
        wait_identity(http)
        admin, values = configure(http, ROOT, marker["settings"])
    for name, value in values.items():
        write(ROOT / "service/secrets" / name, value)
    service = ROOT / "service/service.json"
    data = json.loads(service.read_text())
    data["identity_management"] = {
        "client_id": PRODUCT["admin_client_id"],
        "client_secret_file": "secrets/identity_admin_secret",
        "email_enabled": False,
    }
    data["token_introspection"] = {
        "client_id": data["audience"],
        "client_secret_file": "secrets/introspection_secret",
    }
    write(service, data)
    write(ROOT / "maintenance/identity.json", admin)
    write(ROOT / "identity.done", {"complete": True}, 0, 0)


def bootstrap_phase():
    from app.infrastructure.config import Settings
    from app.infrastructure.database import sessions
    from app.infrastructure.models import Employee, IdentityLink
    from app.modules.access.service import bootstrap
    from sqlalchemy import select

    cfg = Settings.load("/state/maintenance/owner.json")
    admin = json.loads((ROOT / "maintenance/identity.json").read_text())
    factory = sessions(cfg)
    try:
        with factory.begin() as db:
            if db.scalar(select(Employee.id).limit(1)) is None:
                bootstrap(db, cfg.issuer, admin["subject"], admin["name"])
            elif (
                db.scalar(
                    select(IdentityLink.employee_id).where(
                        IdentityLink.issuer == cfg.issuer, IdentityLink.subject == admin["subject"]
                    )
                )
                is None
            ):
                raise RuntimeError("数据库已有不匹配员工，停止引导，不修改数据")
    finally:
        factory.kw["bind"].dispose()
    write(ROOT / "bootstrap.done", {"complete": True}, 0, 0)


def verify_phase():
    from database import verify_database

    database_report = verify_database(ROOT)
    data = json.loads((ROOT / "service/service.json").read_text())
    until = time.monotonic() + 120
    with httpx.Client(timeout=5, trust_env=False) as http:
        while time.monotonic() < until:
            try:
                if (
                    http.get(
                        "http://service:8800/health/ready",
                        headers={"Host": urlsplit(data["public_url"]).netloc},
                    ).status_code
                    == 200
                ):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("业务服务未就绪")
        # 业务就绪不代表重启中的身份服务已可用，独立等待公开发现端点。
        identity_until = time.monotonic() + 120
        while time.monotonic() < identity_until:
            try:
                response = http.get(
                    "http://identity:8080/identity/realms/office/.well-known/openid-configuration"
                )
                response.raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            raise RuntimeError("身份服务未就绪")
        if response.json()["issuer"] != data["issuer"]:
            raise RuntimeError("身份公开地址不一致")
    context = ssl.create_default_context(
        cafile=str(ROOT / "service/ca.pem") if (ROOT / "service/ca.pem").exists() else None
    )
    with httpx.Client(timeout=15, trust_env=False, verify=context) as http:
        http.get(data["public_url"] + "/health/ready").raise_for_status()
        discovery = http.get(data["issuer"] + "/.well-known/openid-configuration")
        discovery.raise_for_status()
        if discovery.json()["issuer"] != data["issuer"]:
            raise RuntimeError("HTTPS身份地址核验不一致")
    write(
        ROOT / "result.json",
        {
            "status": "ready",
            "public_url": data["public_url"],
            "identity_url": data["issuer"],
            "identity_verified": False,
            "next_action": "verify_identity",
            "identity_notice": "安装检查不验证员工登录；请通过浏览器或客户端核对当前身份，未登录时再登录或首次改密",
            "database": database_report,
            "allow_lan_http_demo": data.get("allow_lan_http_demo", False),
            "notice": "局域网HTTP验收模式，传输未加密；请勿用于正式业务"
            if data.get("allow_lan_http_demo")
            else "HTTPS",
            "credentials_file": "credentials/first-login.txt",
        },
        0,
        0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=[
            "parameters",
            "prepare",
            "database",
            "identity",
            "bootstrap",
            "database-check",
            "verify",
        ],
    )
    parser.add_argument("--host-root")
    parser.add_argument("--source")
    parser.add_argument("--image")
    parser.add_argument("--infrastructure")
    parser.add_argument("--pg-uid", type=int, default=999)
    parser.add_argument("--pg-gid", type=int, default=999)
    parser.add_argument("--kc-uid", type=int, default=1000)
    parser.add_argument("--kc-gid", type=int, default=0)
    args = parser.parse_args()
    if args.phase in ("parameters", "prepare"):
        input_file = Path("/input/settings.json")
        if input_file.read_text(encoding="utf-8-sig").lstrip().startswith("{"):
            value = json.loads(input_file.read_text(encoding="utf-8-sig"))
            delivery = {}
        else:
            delivery = read_env(input_file)
            value = server_settings(delivery)
        cfg = settings(value)
        if args.phase == "parameters":
            print(delivery.get("OFFICE_INSTANCE_DIR") or "runtime/instance")
            print(delivery.get("OFFICE_IMAGE", ""))
            for key in ("certificate_file", "private_key_file", "ca_bundle_file"):
                name = cfg.get(key, "")
                if name and (not Path(name).is_absolute() or any(c in name for c in "\r\n:")):
                    raise ValueError("证书路径必须为单行绝对路径")
                print(name)
            external = cfg.get("external_database") or {}
            for key in ("owner_password_file", "app_password_file", "identity_password_file"):
                print(external.get(key, ""))
            return
        prepare(
            ROOT,
            args.host_root,
            args.source,
            value,
            args.image,
            args.pg_uid,
            args.pg_gid,
            args.kc_uid,
            args.kc_gid,
            json.loads(args.infrastructure) if args.infrastructure else None,
        )
    elif args.phase == "database":
        from database import initialize_external

        marker = json.loads((ROOT / "installation.json").read_text())
        if marker["settings"].get("external_database"):
            initialize_external(ROOT, marker["settings"]["external_database"])
    elif args.phase == "database-check":
        from database import verify_database

        write(ROOT / "database-check.json", verify_database(ROOT), 0, 0)
    elif args.phase == "identity":
        identity_phase()
    elif args.phase == "bootstrap":
        bootstrap_phase()
    else:
        verify_phase()
    print("INSTALL_PHASE_OK " + args.phase)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- 安装边界统一脱敏，不打印上游正文。
        # httpx响应可能包含身份管理资料，禁止打印repr/traceback或HTTP正文。
        print(
            "安装阶段 "
            + sys.argv[1]
            + " 失败："
            + type(error).__name__
            + "；已保留实例，请核对配置与该栈日志",
            file=sys.stderr,
        )
        sys.exit(1)
