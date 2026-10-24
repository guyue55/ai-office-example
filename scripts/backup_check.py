"""导出本机开发库并恢复到新库核对；不覆盖、不删除任何数据库。"""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime

from dev_stack import COMPOSE, RUNTIME


def run(*args, **kwargs):
    return subprocess.run(COMPOSE + ["exec", "-T", "postgres", *args], check=True, **kwargs)


def main():
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    target = "office_restore_" + stamp
    directory = RUNTIME / "foundation/backups"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    backup = directory / (target + ".dump")
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        run(
            "bash",
            "-c",
            'export PGPASSWORD="$(cat /run/secrets/owner_password)"; exec pg_dump -h 127.0.0.1 -U office_owner -d office -Fc',
            stdout=stream,
        )
    run("createdb", "-U", "postgres", "-O", "office_owner", target)
    with backup.open("rb") as stream:
        run(
            "bash",
            "-c",
            'export PGPASSWORD="$(cat /run/secrets/owner_password)"; exec pg_restore -h 127.0.0.1 -U office_owner --exit-on-error --no-owner -d "$1"',
            "restore",
            target,
            stdin=stream,
        )
    fields = [
        "'employees',(SELECT count(*) FROM office.employee)",
        "'records',(SELECT count(*) FROM office.record)",
        "'audits',(SELECT count(*) FROM office.audit_event)",
        "'revision',(SELECT revision FROM office.access_revision WHERE id=1)",
        "'schema_revision',(SELECT version_num FROM office.alembic_version)",
    ]
    has_confirmations = (
        run(
            "psql",
            "-U",
            "postgres",
            "-d",
            "office",
            "-tAc",
            "SELECT 1 FROM pg_tables WHERE schemaname='office' AND tablename='record_confirmation'",
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "1"
    )
    if has_confirmations:
        fields.append("'confirmations',(SELECT count(*) FROM office.record_confirmation)")
    query = "SELECT json_build_object(" + ",".join(fields) + ")"

    source = run(
        "psql", "-U", "postgres", "-d", "office", "-tAc", query, capture_output=True, text=True
    ).stdout.strip()
    restored = run(
        "psql", "-U", "postgres", "-d", target, "-tAc", query, capture_output=True, text=True
    ).stdout.strip()
    if source != restored:
        raise RuntimeError("备份恢复核对不一致；保留现场，不删除数据")
    report = {
        "status": "passed",
        "restore_database": target,
        "counts": json.loads(source),
        "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "scope": "本地开发库同实例新库恢复，不代替异机灾备验收",
    }
    (RUNTIME / "foundation/backup-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
