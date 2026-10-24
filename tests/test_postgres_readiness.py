"""用真实PG18临时初始化服务器复现假就绪；不接既有数据库。"""

import os
import secrets
import shlex
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from test_query_postgres import docker

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.live


def test_compose_readiness_rejects_temporary_initialization_server(tmp_path):
    if os.environ.get("OFFICE_QUERY_PG_TESTS") != "1":
        pytest.skip("需显式OFFICE_QUERY_PG_TESTS=1创建独立PG18")
    password = tmp_path / "password"
    password.write_text(secrets.token_urlsafe(32))
    password.chmod(0o600)
    gate = tmp_path / "gate.sh"
    gate.write_text(
        "#!/bin/sh\nset -eu\ntouch /tmp/office-initializing\n"
        "for i in $(seq 1 300); do\n"
        "  test ! -e /tmp/office-release || break\n"
        "  sleep 0.1\ndone\ntest -e /tmp/office-release\n"
    )
    gate.chmod(0o644)
    name = "office-readiness-test-" + uuid4().hex[:12]

    def probe(*args):
        return subprocess.run(
            ["docker", "exec", name, *args], capture_output=True, check=False, timeout=5
        ).returncode

    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--tmpfs",
            "/var/lib/postgresql:rw",
            "-e",
            "POSTGRES_PASSWORD_FILE=/run/secrets/password",
            "-v",
            f"{password}:/run/secrets/password:ro",
            "-v",
            f"{gate}:/run/office-gate:ro",
            "--entrypoint",
            "sh",
            "pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0",
            "-c",
            "cp /run/office-gate /docker-entrypoint-initdb.d/99-gate.sh; "
            "chmod 0644 /docker-entrypoint-initdb.d/99-gate.sh; "
            "exec docker-entrypoint.sh postgres",
        )
        deadline = time.monotonic() + 20
        while probe("test", "-e", "/tmp/office-initializing") != 0:
            assert time.monotonic() < deadline, "初始化阶段未出现，不能证明就绪反例"
            time.sleep(0.1)
        assert probe("pg_isready", "-U", "postgres") == 0, "旧Unix探针应在临时阶段误报成功"
        health = yaml.safe_load((ROOT / "deploy/compose.dev.yaml").read_text())["services"][
            "postgres"
        ]["healthcheck"]["test"]
        assert health[0] == "CMD-SHELL"
        # 执行部署文件真实探针，不能在测试里另写一个正确条件制造自证。
        assert probe(*shlex.split(health[1])) != 0, "部署探针将临时初始化服务器误认成正式就绪"
        docker("exec", name, "touch", "/tmp/office-release")
        deadline = time.monotonic() + 20
        while probe(*shlex.split(health[1])) != 0:
            assert time.monotonic() < deadline, "最终TCP服务器未就绪"
            time.sleep(0.1)
        assert (
            docker(
                "exec",
                name,
                "sh",
                "-c",
                'PGPASSWORD="$(cat /run/secrets/password)" psql -h 127.0.0.1 -U postgres -tAc "SELECT 1"',
            )
            == "1"
        )
    except BaseException:
        diagnostic = subprocess.run(
            ["docker", "logs", name], capture_output=True, text=True, check=False, timeout=5
        )
        # 仅本轮随机PG日志；秘密若意外出现仍先替换，不输出配置/命令正文。
        safe = (diagnostic.stdout + diagnostic.stderr).replace(password.read_text(), "[REDACTED]")
        print(safe[-1600:])
        raise
    finally:
        try:
            docker("rm", "-f", name)
        finally:
            password.unlink(missing_ok=True)
