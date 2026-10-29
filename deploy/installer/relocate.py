"""受限的同端口HTTP演示实例迁址；先预检／备份，失败保留停写现场。"""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from layout import nginx, settings, write


def command(args, **kwargs):
    kwargs.setdefault("timeout", 180)
    result = subprocess.run(args, check=False, capture_output=True, **kwargs)
    if result.returncode:
        for line in result.stdout.decode(errors="replace").splitlines():
            if line.startswith("迁址阶段失败："):
                raise RuntimeError(line)
        raise RuntimeError("维护命令失败；已保留停写现场，请核对阶段记录")
    return result.stdout


def plan(root, url, listen, image):
    marker = json.loads((root / "installation.json").read_text())
    compose = json.loads((root / "compose.json").read_text())
    old = marker["settings"]
    if (
        not old.get("allow_lan_http_demo")
        or old.get("external_database")
        or "postgres" not in compose["services"]
    ):
        raise ValueError("当前维护入口仅支持内置数据库的HTTP演示实例；HTTPS／外部数据库另行规划")
    allowed = {
        "public_url",
        "listen_address",
        "admin_username",
        "admin_name",
        "allow_lan_http_demo",
    }
    cfg = settings(
        {
            **{k: v for k, v in old.items() if k in allowed},
            "public_url": url,
            "listen_address": listen,
        }
    )
    if cfg["public_url"] == old["public_url"] or cfg["port"] != old["port"]:
        raise ValueError("只允许改动公开地址与监听范围，且必须保持原端口")
    if marker["project"] != "office-" + hashlib.sha256(str(root).encode()).hexdigest()[:12]:
        raise ValueError("实例目录与项目标识不匹配")
    image_id = (
        command(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).decode().strip()
    )
    service = json.loads((root / "service/service.json").read_text())
    owner = json.loads((root / "maintenance/owner.json").read_text())
    for data in (service, owner):
        if (
            data["public_url"] != old["public_url"]
            or data["issuer"] != old["public_url"] + "/identity/realms/office"
            or data["audience"] != old["public_url"] + "/mcp"
        ):
            raise ValueError("已有配置不一致，停止自动迁址")
    old_audience = old["public_url"] + "/mcp"
    if service.get("token_introspection", {}).get("client_id") != old_audience:
        raise ValueError("原受众内省配置不匹配")
    for data in (service, owner):
        data.update(
            public_url=cfg["public_url"],
            issuer=cfg["public_url"] + "/identity/realms/office",
            audience=cfg["public_url"] + "/mcp",
        )
    service["token_introspection"]["client_id"] = service["audience"]
    compose["services"]["identity"]["environment"]["KC_HOSTNAME"] = cfg["public_url"] + "/identity"
    compose["services"]["gateway"]["ports"] = [
        f"{cfg['listen_address']}:{cfg['port']}:{cfg['port']}"
    ]
    for name in ("service", "setup"):
        compose["services"][name]["image"] = image_id
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*")):
        if path.is_file() and path.suffix in {".py", ".sh"}:
            digest.update(path.name.encode() + path.read_bytes())
    marker.update(image=image_id, installer_sha256=digest.hexdigest(), settings={**old, **cfg})
    return {
        "old": old["public_url"],
        "new": cfg["public_url"],
        "project": marker["project"],
        "image": image_id,
        "files": {
            "installation.json": marker,
            "compose.json": compose,
            "service/service.json": service,
            "maintenance/owner.json": owner,
            "gateway/nginx.conf": nginx(cfg),
        },
    }


def compose_args(root, project):
    return ["docker", "compose", "-p", project, "-f", str(root / "compose.json")]


def phase(root, operation, info, run_id):
    override = root / ".relocation-tool.json"
    write(override, {"services": {"setup": {"image": info["image"]}}}, 0, 0)
    args = (
        compose_args(root, info["project"])
        + ["-f", str(override)]
        + [
            "--profile",
            "setup",
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "setup",
            "/installer/relocation_phases.py",
            operation,
            "--old",
            info["old"],
            "--new",
            info["new"],
            "--run-id",
            run_id,
        ]
    )
    return json.loads(command(args))


def dump_databases(project, folder):
    pg = project + "-postgres-1"
    for database in ("office", "office_identity"):
        with (folder / (database + ".dump")).open("xb") as output:
            os.chmod(output.name, 0o600)
            subprocess.run(
                [
                    "docker",
                    "exec",
                    pg,
                    "sh",
                    "-c",
                    'export PGPASSWORD="$(cat /run/secrets/postgres_password)"; exec pg_dump -h 127.0.0.1 -U postgres -d "$1" -Fc',
                    "office-backup",
                    database,
                ],
                check=True,
                stdout=output,
                stderr=subprocess.PIPE,
            )
        with (folder / (database + ".dump")).open("rb") as stream:
            command(["docker", "exec", "-i", pg, "pg_restore", "--list"], stdin=stream)


def apply(root, info):
    pending = root / ".relocation-pending.json"
    if pending.exists():
        raise ValueError("存在未完成迁址；先按受限备份和阶段记录恢复，禁止再次叠加")
    run_id = str(uuid4())
    folder = root / "relocations" / run_id
    folder.mkdir(parents=True, mode=0o700)
    folder.parent.chmod(0o700)
    journal = {
        "id": run_id,
        "old": info["old"],
        "new": info["new"],
        "project": info["project"],
        "phase": "preflight",
        "created_at": datetime.now(UTC).isoformat(),
    }
    write(pending, journal, 0, 0)
    stopped = False
    try:
        proof = phase(root, "preflight", info, run_id)
        write(folder / "preflight.json", proof, 0, 0)
        ownership = {}
        for name in (
            "installation.json",
            "compose.json",
            "service/service.json",
            "maintenance/owner.json",
            "gateway/nginx.conf",
        ):
            stat = (root / name).stat()
            ownership[name] = {"uid": stat.st_uid, "gid": stat.st_gid, "mode": stat.st_mode & 0o777}
            dest = folder / "files" / name
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(root / name, dest)
        write(folder / "ownership.json", ownership, 0, 0)
        # 管理密钥保留原文件；备份数据只写受限目录，不输出数据或密码。
        args = compose_args(root, info["project"])
        stopped = True
        command(args + ["stop", "service", "gateway", "identity"])
        dump_databases(info["project"], folder)
        journal["phase"] = "backed_up"
        write(pending, journal, 0, 0)
        for name, value in info["files"].items():
            path = root / name
            stat = path.stat()
            if name == "gateway/nginx.conf":
                value = value.replace(
                    "client_max_body_size 2m;",
                    'client_max_body_size 2m;\n    if ($uri !~ "^/(health/(live|ready)|identity/realms/office/[.]well-known/openid-configuration)$") { return 503; }',
                )
            write(path, value, stat.st_uid, stat.st_gid)
        journal["phase"] = "files_updated"
        write(pending, journal, 0, 0)
        command(args + ["config", "--quiet"])
        command(args + ["up", "-d", "--no-deps", "identity", "gateway"])
        after = phase(root, "apply", info, run_id)
        if after != proof:
            raise RuntimeError("迁址前后用户或业务基线不一致")
        journal["phase"] = "identity_and_database_updated"
        write(pending, journal, 0, 0)
        command(args + ["up", "-d", "--no-deps", "service"])
        command(args + ["--profile", "setup", "run", "--rm", "--no-deps", "setup", "verify"])
        # 先在维护门后核验；从尝试开放入口开始，不再允许无条件恢复数据库快照。
        journal["write_reopened"] = True
        write(pending, journal, 0, 0)
        path = root / "gateway/nginx.conf"
        stat = path.stat()
        write(path, info["files"]["gateway/nginx.conf"], stat.st_uid, stat.st_gid)
        command(args + ["up", "-d", "--no-deps", "--force-recreate", "gateway"])
        command(args + ["--profile", "setup", "run", "--rm", "--no-deps", "setup", "verify"])
        journal["phase"] = "verified"
        write(folder / "result.json", journal, 0, 0)
        marker = json.loads((root / "installation.json").read_text())
        marker["last_relocation"] = run_id
        write(root / "installation.json", marker, 0, 0)
        pending.unlink()
        print(
            json.dumps(
                {
                    "status": "verified",
                    "public_url": info["new"],
                    "backup": str(folder),
                    "relogin_required": True,
                }
            )
        )
    except Exception:
        if stopped:
            command(
                compose_args(root, info["project"]) + ["stop", "service", "gateway", "identity"]
            )
        journal["failed_after"] = journal["phase"]
        journal["phase"] = "failed"
        write(pending, journal, 0, 0)
        if not stopped:
            write(folder / "result.json", journal, 0, 0)
            pending.unlink()
        raise


def restore(root, run_id):
    pending = root / ".relocation-pending.json"
    journal = json.loads(pending.read_text())
    if (
        journal["id"] != run_id
        or journal["phase"] != "failed"
        or journal.get("write_reopened")
        or str(UUID(run_id)) != run_id
    ):
        raise ValueError("只允许恢复同一次失败且停写的迁址，不能回滚已开放写入的实例")
    folder = root / "relocations" / run_id
    ownership = json.loads((folder / "ownership.json").read_text())
    args = compose_args(root, journal["project"])
    command(args + ["stop", "service", "gateway", "identity"])
    pg = journal["project"] + "-postgres-1"
    databases = () if journal.get("failed_after") == "preflight" else ("office", "office_identity")
    for database in databases:
        with (folder / (database + ".dump")).open("rb") as stream:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    pg,
                    "sh",
                    "-c",
                    'export PGPASSWORD="$(cat /run/secrets/postgres_password)"; exec pg_restore -h 127.0.0.1 -U postgres -d "$1" --clean --if-exists --exit-on-error --single-transaction',
                    "office-restore",
                    database,
                ],
                check=True,
                stdin=stream,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=600,
            )
    for name, stat in ownership.items():
        dest = root / name
        shutil.copy2(folder / "files" / name, dest)
        os.chown(dest, stat["uid"], stat["gid"])
        dest.chmod(stat["mode"])
    journal["phase"] = "rolled_back"
    write(folder / "result.json", journal, 0, 0)
    pending.unlink()
    print(
        json.dumps(
            {
                "status": "restored",
                "services_stopped": True,
                "reason": "原地址仍需管理员确认可用后再启动",
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--listen")
    parser.add_argument("--image")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore", help="只恢复同一次失败迁址；须同时--apply，恢复后保持停写")
    args = parser.parse_args()
    root = args.instance.resolve(strict=True)
    if args.instance.is_symlink():
        raise ValueError("实例目录不能是软链")
    with (root / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.restore:
            if not args.apply:
                raise ValueError("恢复数据库需要显式--apply")
            restore(root, args.restore)
            return
        if not all((args.url, args.listen, args.image)):
            raise ValueError("迁址必须指定--url、--listen和--image")
        info = plan(root, args.url, args.listen, args.image)
        if not args.apply:
            phase(root, "preflight", info, str(uuid4()))
            print(
                json.dumps(
                    {
                        **{k: info[k] for k in ("old", "new", "project", "image")},
                        "preflight": "passed",
                    }
                )
            )
            return
        apply(root, info)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- 维护边界统一脱敏，不打印上游或秘密。
        print(
            "迁址未完成："
            + (
                str(error)
                if isinstance(error, (ValueError, RuntimeError))
                else type(error).__name__
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
