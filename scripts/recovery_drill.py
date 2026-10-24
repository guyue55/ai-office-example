"""本机双库隔离恢复：不停止源服务、不覆盖数据库、不输出秘密或登录令牌。"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import yaml
from app.application.service import Application
from app.infrastructure.config import Settings
from app.infrastructure.database import async_sessions
from app.modules.access.authentication import Principal
from dev_stack import COMPOSE, ROOT
from docker_smoke import docker
from psycopg import sql
from recovery_oidc import RestoredIdentityTransport, restored_login


def protected_write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(value)


def fingerprint(connection, schema):
    rows = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (schema,)
    ).fetchall()
    result = {}
    for (name,) in rows:
        query = sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(md5(row_to_json(t)::text), '' ORDER BY md5(row_to_json(t)::text)),'')) FROM {}.{} AS t"
        ).format(sql.Identifier(schema), sql.Identifier(name))
        count, digest = connection.execute(query).fetchone()
        result[name] = {"rows": count, "digest": digest}
    return result


def source_dump(source, dbname, schema, destination, password):
    with psycopg.connect(
        host="127.0.0.1",
        port=55432,
        user="postgres",
        password=password,
        dbname=dbname,
        connect_timeout=5,
    ) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        connection.execute("SET LOCAL statement_timeout='30s'")
        snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        expected = fingerprint(connection, schema)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    source,
                    "pg_dump",
                    "-U",
                    "postgres",
                    "-d",
                    dbname,
                    "-Fc",
                    "--snapshot=" + snapshot,
                ],
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=180,
                check=False,
            )
        if result.returncode:
            raise RuntimeError("源库快照导出失败；未回显数据库内容")
        return expected


def wait_ready(probe, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if probe():
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("隔离恢复实例启动超时")


async def verify_business(settings, claims):
    factory = async_sessions(settings)
    application = Application(settings, factory, None)
    principal = Principal(settings.issuer, claims["sub"], claims)
    try:
        me = await application.call(principal, "me", {}, str(uuid4()))
        projects = await application.call(principal, "project.list", {"limit": 1}, str(uuid4()))
        records = await application.call(principal, "record.list", {"limit": 1}, str(uuid4()))
        if "access.manage" not in me["permissions"]:
            raise RuntimeError("恢复后的测试管理员权限不完整")
        return {
            "identity_mapping": True,
            "permissions": True,
            "projects_read": isinstance(projects["items"], list),
            "records_read": isinstance(records["items"], list),
        }
    finally:
        await factory.kw["bind"].dispose()


def create_backup(source_settings, folder):
    secrets_dir = folder / "secrets"
    secrets_dir.mkdir(mode=0o700)
    config = source_settings.model_dump(mode="json")
    references = [source_settings.database_password_file, source_settings.web_session_secret_file]
    for field in ("identity_management", "token_introspection"):
        value = getattr(source_settings, field)
        if value:
            references.append(value.client_secret_file)
    originals = ROOT / "runtime/secrets"
    references.extend(
        originals / field
        for field in ("postgres_password", "owner_password", "app_password", "identity_password")
    )
    for reference in references:
        if reference is None:
            continue
        reference = reference.resolve()
        if not reference.is_relative_to(originals.resolve()) or reference.stat().st_mode & 0o077:
            raise RuntimeError("恢复秘密必须来自本机受限秘密目录")
        target = secrets_dir / reference.relative_to(originals.resolve())
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            protected_write(target, reference.read_text())
    for field in ("database_password_file", "web_session_secret_file"):
        if config.get(field):
            config[field] = "secrets/" + str(
                Path(config[field]).resolve().relative_to(originals.resolve())
            )
    for field in ("identity_management", "token_introspection"):
        if config.get(field):
            config[field]["client_secret_file"] = "secrets/" + str(
                Path(config[field]["client_secret_file"]).resolve().relative_to(originals.resolve())
            )
    protected_write(folder / "service.json", json.dumps(config, ensure_ascii=False, indent=2))
    source = subprocess.check_output([*COMPOSE, "ps", "-q", "postgres"], text=True).strip()
    if not source:
        raise RuntimeError("本机源PG容器未启动")
    expected = {}
    for dbname, schema in (("office", "office"), ("office_identity", "public")):
        expected[dbname] = source_dump(
            source,
            dbname,
            schema,
            folder / (dbname + ".dump"),
            (secrets_dir / "postgres_password").read_text().strip(),
        )
    protected_write(
        folder / "manifest.json",
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "source_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "snapshots": expected,
                "dumps": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in folder.glob("*.dump")
                },
            },
            indent=2,
        ),
    )
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, help="复用既有受限备份，只创建新的恢复容器")
    parser.add_argument(
        "--browser-executable", help="已有浏览器路径；默认使用Playwright已有浏览器，不自动安装"
    )
    parser.add_argument(
        "--verify-session-policy",
        action="store_true",
        help="仅在临时恢复身份实例验证短周期会话策略",
    )
    args = parser.parse_args()
    source_paths = [
        *sorted((ROOT / "office-data-service/app").rglob("*.py")),
        *sorted((ROOT / "clients/python/src/office_client").glob("*.py")),
        ROOT / "scripts/recovery_drill.py",
        ROOT / "scripts/recovery_oidc.py",
        ROOT / "scripts/session_policy.py",
        ROOT / "scripts/session_policy_probe.py",
        ROOT / "scripts/configure_session_policy.py",
        ROOT / "deploy/identity/session-policy.json",
    ]
    fingerprints = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths
    }
    source_settings = Settings.load(str(ROOT / "runtime/foundation/config.json"))
    if (
        source_settings.database_host,
        source_settings.database_port,
        source_settings.database_name,
        source_settings.issuer,
    ) != ("127.0.0.1", 55432, "office", "http://127.0.0.1:8081/realms/office"):
        raise RuntimeError("恢复辅助脚本只允许本机独立开发实例")
    suffix = uuid4().hex[:12]
    name = "office-recovery-" + suffix
    print("RECOVERY_ENV " + name, flush=True)
    folder = ROOT / "runtime/foundation/recovery" / suffix
    if args.backup_dir:
        folder = args.backup_dir.resolve()
        if not folder.is_relative_to((ROOT / "runtime/foundation/recovery").resolve()):
            raise RuntimeError("仅允许恢复本机受限恢复目录中的备份")
        manifest = json.loads((folder / "manifest.json").read_text())
        for dbname in ("office", "office_identity"):
            path = folder / (dbname + ".dump")
            if (
                path.stat().st_mode & 0o077
                or hashlib.sha256(path.read_bytes()).hexdigest() != manifest["dumps"][path.name]
            ):
                raise RuntimeError("备份权限或摘要校验失败")
        expected = manifest["snapshots"]
    else:
        folder.mkdir(parents=True, mode=0o700)
        expected = create_backup(source_settings, folder)
    secrets_dir = folder / "secrets"
    originals = ROOT / "runtime/secrets"
    images = yaml.safe_load((ROOT / "deploy/compose.dev.yaml").read_text())["services"]
    postgres_image, identity_image = images["postgres"]["image"], images["identity"]["image"]
    for image in (postgres_image, identity_image):
        docker("image", "inspect", image)
    print("RECOVERY_BUNDLE " + str(folder.relative_to(ROOT)), flush=True)
    print("RECOVERY_BACKUPS_READY", flush=True)
    created = []
    network_created = False
    succeeded = False
    try:
        docker("network", "create", name)
        network_created = True
        pg = name + "-pg"
        docker(
            "run",
            "--pull=never",
            "-d",
            "--name",
            pg,
            "--network",
            name,
            "--tmpfs",
            "/var/lib/postgresql:rw",
            "-p",
            "127.0.0.1::5432",
            "-v",
            f"{secrets_dir}:/run/secrets:ro",
            "-e",
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password",
            postgres_image,
        )
        created.append(pg)

        def pg_ready():
            return (
                subprocess.run(
                    ["docker", "exec", pg, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                    capture_output=True,
                    check=False,
                ).returncode
                == 0
            )

        wait_ready(pg_ready)
        docker(
            "exec",
            "-i",
            "-e",
            "POSTGRES_USER=postgres",
            pg,
            "bash",
            "-s",
            stdin=(ROOT / "deploy/database/init.sh").read_text(),
        )
        for dbname, role in (("office", "office_owner"), ("office_identity", "office_identity")):
            with (folder / (dbname + ".dump")).open("rb") as stream:
                result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        pg,
                        "pg_restore",
                        "-U",
                        "postgres",
                        "--role=" + role,
                        "--clean",
                        "--if-exists",
                        "--no-owner",
                        "--exit-on-error",
                        "-d",
                        dbname,
                    ],
                    stdin=stream,
                    capture_output=True,
                    timeout=180,
                    check=False,
                )
            if result.returncode:
                protected_write(
                    folder / (dbname + "-restore-error.log"), result.stderr.decode(errors="replace")
                )
                raise RuntimeError("隔离库恢复失败；保留受限备份，未覆盖源库")
        pg_port = int(docker("port", pg, "5432/tcp").rsplit(":", 1)[1])
        for dbname, schema in (("office", "office"), ("office_identity", "public")):
            with psycopg.connect(
                host="127.0.0.1",
                port=pg_port,
                dbname=dbname,
                user="postgres",
                password=(secrets_dir / "postgres_password").read_text().strip(),
            ) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL TIME ZONE 'UTC'")
                if fingerprint(connection, schema) != expected[dbname]:
                    raise RuntimeError("恢复内容与各自导出快照不一致")
        print("RECOVERY_DATABASES_VERIFIED", flush=True)
        identity = name + "-identity"
        docker(
            "run",
            "--pull=never",
            "-d",
            "--name",
            identity,
            "--network",
            name,
            "-p",
            "127.0.0.1::8080",
            "-v",
            f"{secrets_dir}:/run/secrets:ro",
            "-v",
            f"{ROOT / 'deploy/identity/themes/office'}:/opt/keycloak/themes/office:ro",
            "-e",
            "KC_DB=postgres",
            "-e",
            f"KC_DB_URL=jdbc:postgresql://{pg}:5432/office_identity",
            "-e",
            "KC_DB_USERNAME=office_identity",
            "-e",
            "KC_HOSTNAME=http://127.0.0.1:8081",
            "--entrypoint",
            "/bin/bash",
            identity_image,
            "-c",
            'export KC_DB_PASSWORD="$(cat /run/secrets/identity_password)"; exec /opt/keycloak/bin/kc.sh start-dev',
        )
        created.append(identity)
        identity_port = int(docker("port", identity, "8080/tcp").rsplit(":", 1)[1])
        with httpx.Client(
            transport=RestoredIdentityTransport("http://127.0.0.1:8081", identity_port),
            timeout=10,
            follow_redirects=False,
            trust_env=False,
        ) as http:
            wait_ready(
                lambda: (
                    http.get(
                        source_settings.issuer + "/.well-known/openid-configuration"
                    ).status_code
                    == 200
                )
            )
            claims, access_token = restored_login(
                http,
                source_settings,
                "operator",
                (originals / "operator_password").read_text().strip(),
                target_port=identity_port,
                browser_executable=args.browser_executable,
            )
            saved = Settings.load(str(folder / "service.json"))
            connection_checks = {}
            if saved.token_introspection:
                config = saved.token_introspection
                response = http.post(
                    saved.issuer + "/protocol/openid-connect/token/introspect",
                    data={
                        "client_id": config.client_id,
                        "client_secret": config.client_secret_file.read_text().strip(),
                        "token": access_token,
                        "token_type_hint": "access_token",
                    },
                )
                response.raise_for_status()
                if response.json().get("active") is not True:
                    raise RuntimeError("恢复后的在线校验客户端不可用")
                connection_checks["introspection_credentials"] = True
            if saved.identity_management:
                config = saved.identity_management
                response = http.post(
                    saved.issuer + "/protocol/openid-connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": config.client_id,
                        "client_secret": config.client_secret_file.read_text().strip(),
                    },
                )
                response.raise_for_status()
                admin_token = response.json()["access_token"]
                response = http.get(
                    "http://127.0.0.1:8081/admin/realms/office/users",
                    params={"max": 1},
                    headers={"Authorization": "Bearer " + admin_token},
                )
                response.raise_for_status()
                connection_checks["identity_management_credentials"] = True
            if args.verify_session_policy:
                from session_policy_probe import verify_session_policy

                connection_checks["session_policy"] = verify_session_policy(
                    http, source_settings, identity_port, args.browser_executable
                )
        restored = Settings.load(str(folder / "service.json")).model_copy(
            update={
                "database_host": "127.0.0.1",
                "database_port": pg_port,
                "identity_management": None,
                "token_introspection": None,
            }
        )
        checks = asyncio.run(verify_business(restored, claims))
        if fingerprints != {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_paths
        }:
            raise RuntimeError("验收期间源码发生变化，拒绝封账，请重新验证")
        protected_write(
            folder / ("result-" + suffix + ".json"),
            json.dumps(
                {
                    "status": "passed",
                    "verified_at": datetime.now(UTC).isoformat(),
                    "images": {"postgres": postgres_image, "identity": identity_image},
                    "source_frozen_before_run": True,
                    "verification_source": fingerprints,
                    "checks": {
                        "two_database_fingerprints": True,
                        "restored_oauth_pkce": True,
                        **connection_checks,
                        **checks,
                    },
                    "boundary": "本机隔离恢复；两库各自一致性快照，不代表生产跨库原子备份或异机RPO/RTO验收",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        succeeded = True
        print(
            "ISOLATED_RECOVERY_OK two_databases oauth_pkce identity_mapping permissions records config_secrets"
        )
    finally:
        if not succeeded and created and created[-1].endswith("-identity"):
            result = subprocess.run(
                ["docker", "logs", created[-1]], capture_output=True, text=True, check=False
            )
            labels = sorted(set(re.findall(r'error="([a-z_]+)"', result.stdout + result.stderr)))
            print("RECOVERY_IDENTITY_ERROR_LABELS " + ",".join(labels))
        for container in reversed(created):
            docker("rm", "-f", container)
        if network_created:
            docker("network", "rm", name)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- 防止临时OAuth凭据进入错误输出
        # OAuth响应可能含临时凭据；不得直接输出异常正文和HTTP响应。
        print("恢复验收未完成，错误类型：" + type(error).__name__)
        if type(error) is RuntimeError:
            print(str(error))
        raise SystemExit(1) from None
