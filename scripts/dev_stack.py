"""本机基础环境管理：幂等建库和身份配置，不删除卷、不打印密钥。"""

import argparse
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import httpx
from app.common.product import PRODUCT
from identity_clients import ensure_client

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "deploy/compose.dev.yaml"),
]


def command(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def secret(name):
    return (RUNTIME / "secrets" / name).read_text().strip()


def prepare():
    directory = RUNTIME / "secrets"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    for name in [
        "postgres_password",
        "owner_password",
        "app_password",
        "identity_password",
        "admin_password",
        "operator_password",
        "employee_password",
        "web_session_secret",
    ]:
        path = directory / name
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(32))
    (RUNTIME / "foundation").mkdir(exist_ok=True)
    port = int(os.environ.get("OFFICE_DEV_PORT", "8800"))
    if not 1 <= port <= 65535:
        raise ValueError("OFFICE_DEV_PORT超出范围")
    public_url = "http://127.0.0.1:" + str(port)
    config = {
        "database_user": "office_app",
        "database_password_file": str(directory / "app_password"),
        "database_host": "127.0.0.1",
        "database_port": 55432,
        "database_name": "office",
        "issuer": "http://127.0.0.1:8081/realms/office",
        "audience": public_url + "/mcp",
        "public_url": public_url,
        "client_id": PRODUCT["client_id"],
        "web_client_id": PRODUCT["web_client_id"],
        "redirect_uri": "http://127.0.0.1:8765/callback",
        "web_session_secret_file": str(directory / "web_session_secret"),
    }
    config = preserve_config(RUNTIME / "foundation/config.json", config)
    owner = {
        **config,
        "database_user": "office_owner",
        "database_password_file": str(directory / "owner_password"),
    }
    preserve_config(RUNTIME / "foundation/owner.json", owner)


def preserve_config(path, defaults):
    """仅补缺项；坏文件失败并保留现场，不能重置用户现有设置。"""
    current = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(current, dict):
        raise TypeError("配置文件必须为对象，原文件未修改")
    merged = {**defaults, **current}
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(json.dumps(merged, indent=2))
    temporary.replace(path)
    return merged


def wait_for(url, seconds=600):
    """独立开发身份冷启动可包含编译／迁移；按墙钟预算等待，及时成功返回。"""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            if httpx.get(url, timeout=min(2, remaining)).is_success:
                return
        except httpx.HTTPError:
            pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1, remaining))
    raise RuntimeError("本地身份服务未就绪，请检查本项目容器日志")


def up():
    prepare()
    command(COMPOSE + ["up", "-d", "postgres"])
    for _ in range(30):
        ready = subprocess.run(
            COMPOSE + ["exec", "-T", "postgres", "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
            capture_output=True,
            check=False,
        )
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("本机PostgreSQL正式TCP服务未就绪，停止初始化")
    exists = command(
        COMPOSE
        + [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname='office'",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not exists:
        # 显式通过 bash stdin 执行，避开 macOS 绑定挂载的 noexec 行为。
        command(
            COMPOSE + ["exec", "-T", "-e", "POSTGRES_USER=postgres", "postgres", "bash", "-s"],
            input=(ROOT / "deploy/database/init.sh").read_text(),
            text=True,
            stdout=subprocess.DEVNULL,
        )
    command(COMPOSE + ["up", "-d", "identity"])
    wait_for("http://127.0.0.1:8081/realms/master/.well-known/openid-configuration")


def provision():
    """只管理专用开发 realm，不覆盖已有用户密码或业务授权。"""
    with httpx.Client(base_url="http://127.0.0.1:8081", timeout=90) as http:
        token_response = http.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "foundation-admin",
                "password": secret("admin_password"),
            },
        )
        token_response.raise_for_status()
        http.headers["Authorization"] = "Bearer " + token_response.json()["access_token"]
        realm_path = "/admin/realms/office"
        if http.get(realm_path).status_code == 404:
            response = http.post(
                "/admin/realms",
                json={
                    "realm": "office",
                    "enabled": True,
                    "registrationAllowed": False,
                    "internationalizationEnabled": True,
                    "supportedLocales": ["zh-CN", "en"],
                    "defaultLocale": "zh-CN",
                },
            )
            response.raise_for_status()
        scopes = http.get(realm_path + "/client-scopes").json()
        scope = next((s for s in scopes if s["name"] == "office"), None)
        if scope is None:
            response = http.post(
                realm_path + "/client-scopes",
                json={
                    "name": "office",
                    "protocol": "openid-connect",
                    "attributes": {"include.in.token.scope": "true"},
                },
            )
            response.raise_for_status()
            scope = next(
                s for s in http.get(realm_path + "/client-scopes").json() if s["name"] == "office"
            )
        config = json.loads((RUNTIME / "foundation/config.json").read_text())
        for client_id, redirect in (
            (config["client_id"], config["redirect_uri"]),
            (config.get("web_client_id", "office-web"), config["public_url"] + "/auth/callback"),
        ):
            ensure_client(http, realm_path, scope["id"], client_id, redirect, config["audience"])
        identities = {}
        for username, password_file in [
            ("operator", "operator_password"),
            ("employee", "employee_password"),
        ]:
            users = http.get(
                realm_path + "/users", params={"username": username, "exact": "true"}
            ).json()
            if not users:
                response = http.post(
                    realm_path + "/users",
                    json={
                        "username": username,
                        "enabled": True,
                        "emailVerified": True,
                        "firstName": "开发",
                        "lastName": "测试",
                        "email": username + "@example.invalid",
                        "credentials": [
                            {"type": "password", "value": secret(password_file), "temporary": False}
                        ],
                    },
                )
                response.raise_for_status()
                users = http.get(
                    realm_path + "/users", params={"username": username, "exact": "true"}
                ).json()
            identities[username] = {"subject": users[0]["id"], "name": "本地测试" + username}
        (RUNTIME / "foundation/identities.json").write_text(json.dumps(identities, indent=2))
        (RUNTIME / "foundation/operator.json").write_text(json.dumps(identities["operator"]))
    print("开发身份配置完成；凭证仅保留在忽略目录。")


def migrate():
    env = {**os.environ, "OFFICE_CONFIG": str(RUNTIME / "foundation/owner.json")}
    command(
        ["uv", "run", "alembic", "-c", "office-data-service/alembic.ini", "upgrade", "head"],
        env=env,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "stop", "status", "provision", "migrate"])
    args = parser.parse_args()
    if args.action == "up":
        up()
    elif args.action == "provision":
        provision()
    elif args.action == "migrate":
        migrate()
    else:
        command(COMPOSE + (["stop"] if args.action == "stop" else ["ps"]))


if __name__ == "__main__":
    main()
