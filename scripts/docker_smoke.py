"""独立容器验收：仅创建本次临时资源，不连接现有开发库、不删除任何卷。"""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, stdin=None):
    result = subprocess.run(
        ["docker", *args], input=stdin, text=True, capture_output=True, check=False
    )
    if result.returncode:
        # 配置/SQL输入含测试秘密，失败时也不回显原始命令响应。
        raise RuntimeError(f"容器操作失败：{args[0]}，请在独立环境排查")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="office-data-service:local")
    args = parser.parse_args()
    suffix = uuid4().hex[:12]
    network, postgres, service = ("office-ci-" + suffix + end for end in ("", "-pg", "-web"))
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise RuntimeError("验收须由非root用户运行，验证非root密钥挂载权限")
    image = json.loads(docker("image", "inspect", args.image))[0]
    assert image["Config"]["User"] == "10001:10001"
    with tempfile.TemporaryDirectory(prefix="office-docker-") as temp:
        folder = Path(temp)
        for name in ("postgres_password", "owner_password", "app_password", "identity_password"):
            path = folder / name
            path.write_text(secrets.token_urlsafe(32))
            path.chmod(0o600)
        common = {
            "database_host": postgres,
            "database_port": 5432,
            "database_name": "office",
            "issuer": "https://identity.example/realms/office",
            "audience": "https://office.example/mcp",
            "public_url": "https://office.example",
        }
        for name, user, secret in (
            ("service", "office_app", "app_password"),
            ("owner", "office_owner", "owner_password"),
        ):
            (folder / f"{name}.json").write_text(
                json.dumps(
                    {
                        **common,
                        "database_user": user,
                        "database_password_file": secret,
                    }
                )
            )
        (folder / "identity.json").write_text(json.dumps({"subject": suffix, "name": "容器验收"}))
        mount = f"{folder}:/etc/office:ro"
        base = [
            "run",
            "--rm",
            "--network",
            network,
            "--user",
            f"{uid}:{gid}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--tmpfs",
            "/tmp:rw,size=64m",
            "-v",
            mount,
        ]
        try:
            docker("network", "create", network)
            docker(
                "run",
                "-d",
                "--name",
                postgres,
                "--network",
                network,
                "--tmpfs",
                "/var/lib/postgresql:rw",
                "-v",
                f"{folder}:/run/secrets:ro",
                "-e",
                "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password",
                "pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0",
            )
            for _ in range(60):
                result = subprocess.run(
                    ["docker", "exec", postgres, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                    capture_output=True,
                    check=False,
                )
                if result.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("临时PG18启动超时")
            docker(
                "exec",
                "-i",
                "-e",
                "POSTGRES_USER=postgres",
                postgres,
                "bash",
                "-s",
                stdin=(ROOT / "deploy/database/init.sh").read_text(),
            )
            owner = [*base, "-e", "OFFICE_CONFIG=/etc/office/owner.json", args.image]
            docker(
                *owner,
                "python",
                "-m",
                "alembic",
                "-c",
                "/opt/office/alembic.ini",
                "upgrade",
                "head",
            )
            docker(*owner, "python", "-m", "alembic", "-c", "/opt/office/alembic.ini", "check")
            docker(
                *owner,
                "office-service",
                "bootstrap",
                "--identity-file",
                "/etc/office/identity.json",
            )
            docker(*base, "-d", "--name", service, "-p", "127.0.0.1::8800", args.image)
            port = docker("port", service, "8800/tcp").rsplit(":", 1)[1]
            base_url = "http://127.0.0.1:" + port
            with httpx.Client(base_url=base_url, trust_env=False) as client:
                for _ in range(60):
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError("容器服务未就绪")
                assert client.get("/health/live").status_code == 200
                assert client.get("/api/v2/projects").status_code == 401
                assert (
                    client.get("/health/live", headers={"Host": "office.example"}).status_code
                    == 200
                )
                assert (
                    client.get("/health/live", headers={"Host": "evil.example"}).status_code == 400
                )
                assert client.get("/auth/config").json()["issuer"] == common["issuer"]
            docker("stop", "--time", "15", service)
            # --rm容器已删除；不依赖现有开发卷或测试账户。
            print("DOCKER_SMOKE_OK migrations bootstrap readiness auth host shutdown")
        except Exception:
            # 仅保存本次构造环境的诊断，先剔除所有测试秘密；不上传业务资料。
            diagnostics = []
            for name in (service, postgres):
                logs = subprocess.run(
                    ["docker", "logs", name], capture_output=True, text=True, check=False
                )
                content = logs.stdout + logs.stderr
                for secret_file in folder.glob("*_password"):
                    content = content.replace(secret_file.read_text(), "[REDACTED]")
                diagnostics.append(name + "\n" + content)
            output = ROOT / "runtime/foundation/docker-smoke-failure.log"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("\n".join(diagnostics))
            raise
        finally:
            for name in (service, postgres):
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
            subprocess.run(["docker", "network", "rm", network], capture_output=True, check=False)


if __name__ == "__main__":
    main()
