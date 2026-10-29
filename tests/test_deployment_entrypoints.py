"""独立部署入口：人工配置、不依赖Python宿主的服务器引导、客户端地址边界。"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def client(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "client_setup_test", ROOT / "installers/client/setup_client.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(sys.platform == "win32", reason="Bash为服务器入口，Windows员工入口另验")
def test_manual_server_wizard_defaults_and_explicit_http(tmp_path):
    target = tmp_path / "delivery.env"
    answers = "2\nyes\n192.168.1.8\n" + "\n" * 8
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/configure.sh"), str(target)],
        input=answers,
        text=True,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    values = target.read_text()
    assert "OFFICE_PUBLIC_URL=http://192.168.1.8:8800" in values
    assert "OFFICE_ADMIN_USERNAME=admin" in values
    assert "OFFICE_AGENT=0" in values
    assert "OFFICE_IMAGE=" in values
    assert "OFFICE_LISTEN_ADDRESS=lan" in values
    assert "PASSWORD" not in values
    before = target.read_bytes()
    denied = subprocess.run(
        ["bash", str(ROOT / "deploy/configure.sh"), str(target)],
        input="\n",
        text=True,
        check=False,
        capture_output=True,
    )
    assert denied.returncode != 0 and target.read_bytes() == before


def test_management_entrypoint_is_valid_and_documents_all_commands():
    checked = subprocess.run(
        ["bash", "-n", str(ROOT / "manage.sh")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for command in (
        "start",
        "stop",
        "restart",
        "status",
        "check",
        "address",
        "logs",
        "plan",
        "recreate",
        "down",
        "backup",
        "install",
        "build",
        "upgrade",
        "config",
        "preflight",
    ):
        assert command in result.stdout
    assert "--ip" in result.stdout
    detail = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "help", "restart"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert detail.returncode == 0 and "完整重启" in detail.stdout


def test_build_and_upgrade_require_explicit_targets():
    build = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "build"],
        check=False,
        capture_output=True,
        text=True,
    )
    upgrade = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "upgrade"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 2 and "--agent" in build.stderr
    assert upgrade.returncode == 2 and "--image" in upgrade.stderr


def test_manage_config_resolves_relative_paths_without_embedded_root(tmp_path):
    config = tmp_path / "manage.env"
    config.write_text(
        "OFFICE_MANAGE_TIMEOUT=7\n"
        "OFFICE_MANAGE_RUNTIME_DIR=runtime-test\n"
        "OFFICE_MANAGE_BACKUP_DIR=backups-test\n"
        "OFFICE_MANAGE_ACTIVE_INSTANCE=.active-test\n"
        "OFFICE_MANAGE_LOCK_DIR=.lock-test\n"
        'OFFICE_MANAGE_DOCKER_APP="Docker Desktop"\n'
    )
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "config"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_CONFIG": str(config)},
    )
    assert result.returncode == 0, result.stderr
    assert str(ROOT / "runtime-test") in result.stdout
    assert str(ROOT / "runtime-test/backups-test") in result.stdout
    assert "默认等待：7秒" in result.stdout
    assert "Docker应用名：Docker Desktop" in result.stdout


def test_manage_config_rejects_unknown_keys(tmp_path):
    config = tmp_path / "manage.env"
    config.write_text("UNKNOWN=1\n")
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "config"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_CONFIG": str(config)},
    )
    assert result.returncode == 2
    assert "未知键" in result.stderr


def test_manage_config_rejects_parent_path_escape(tmp_path):
    config = tmp_path / "manage.env"
    config.write_text("OFFICE_MANAGE_RUNTIME_DIR=../outside\n")
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "config"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_CONFIG": str(config)},
    )
    assert result.returncode == 2
    assert "父目录跳转" in result.stderr


def test_manage_config_rejects_missing_explicit_file(tmp_path):
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "config"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_CONFIG": str(tmp_path / "absent.env")},
    )
    assert result.returncode == 2
    assert "不存在" in result.stderr


def test_manage_config_rejects_non_numeric_timeout(tmp_path):
    config = tmp_path / "manage.env"
    config.write_text("OFFICE_MANAGE_TIMEOUT=abc\n")
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "config"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_CONFIG": str(config)},
    )
    assert result.returncode == 2
    assert "正整数" in result.stderr


def test_manage_config_supports_inline_comments_and_normalizes_trailing_slash(tmp_path):
    config = tmp_path / "manage.env"
    config.write_text(
        "OFFICE_MANAGE_TIMEOUT=45 # 行尾注释\n"
        'OFFICE_MANAGE_DOCKER_APP="Docker Desktop" # 注释\n'
        "OFFICE_MANAGE_RUNTIME_DIR=runtime-x/\n"
        "OFFICE_MANAGE_BACKUP_DIR=backups-x/\n"
    )
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "config"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_CONFIG": str(config)},
    )
    assert result.returncode == 0, result.stderr
    assert "默认等待：45秒" in result.stdout
    assert "Docker应用名：Docker Desktop" in result.stdout
    assert str(ROOT / "runtime-x/backups-x") in result.stdout
    assert "runtime-x//" not in result.stdout


def test_manage_config_rejects_unbalanced_quote(tmp_path):
    config = tmp_path / "manage.env"
    config.write_text('OFFICE_MANAGE_DOCKER_APP="Docker\n')
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "config"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_CONFIG": str(config)},
    )
    assert result.returncode == 2
    assert "引号未闭合" in result.stderr


def test_manage_install_passes_instance_directory_after_config(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("安装参数回归需要可用的Docker环境")
    probe = subprocess.run(["docker", "info"], check=False, capture_output=True)
    if probe.returncode != 0:
        pytest.skip("Docker引擎未运行，跳过安装参数回归")
    (tmp_path / "deploy").mkdir()
    shutil.copy2(ROOT / "manage.sh", tmp_path / "manage.sh")
    stub = tmp_path / "deploy/install.sh"
    stub.write_text(
        """#!/usr/bin/env bash
for a in "$@"; do printf "ARG:%s\\n" "$a"; done
inst="${2:-}"
[[ -n "$inst" ]] || exit 0
mkdir -p "$inst/service"
printf '{"project": "office-test", "image": "sha256:x", "settings": {}}\\n' > "$inst/installation.json"
printf '{"services": {"service": {"image": "sha256:x"}, "setup": {"image": "sha256:x"}}}\\n' > "$inst/compose.json"
: > "$inst/service/service.json"
"""
    )
    stub.chmod(0o755)
    result = subprocess.run(
        ["bash", str(tmp_path / "manage.sh"), "install", "--instance", "runtime/t1"],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert f"ARG:{tmp_path}/deploy/.env" in result.stdout
    assert "ARG:runtime/t1" in result.stdout


def _backup_fixture(tmp_path, project="office-testfixture"):
    import json as _json

    instance = tmp_path / "instance"
    (instance / "service").mkdir(parents=True)
    (instance / "installation.json").write_text(
        _json.dumps(
            {
                "project": project,
                "image": "sha256:fixture",
                "settings": {
                    "public_url": "http://192.168.1.10:8800",
                    "port": 8800,
                    "allow_lan_http_demo": True,
                    "hostname": "192.168.1.10",
                },
            }
        )
    )
    (instance / "compose.json").write_text(
        _json.dumps(
            {
                "services": {
                    "service": {"image": "sha256:fixture"},
                    "setup": {"image": "sha256:fixture"},
                }
            }
        )
    )
    (instance / "service/service.json").write_text("{}")
    backups = tmp_path / "backups"
    for index in range(1, 6):
        folder = backups / f"{project}-2026010{index}T000000Z-{index}"
        folder.mkdir(parents=True)
        (folder / "MANIFEST.txt").write_text(f"project={project}\n")
        (folder / "office.dump").write_text("x")
    foreign = backups / "other-20260101T000000Z-9"
    foreign.mkdir()
    (foreign / "MANIFEST.txt").write_text("project=other\n")
    return instance, backups, project


def test_prune_keeps_newest_and_leaves_other_projects(tmp_path):
    instance, backups, project = _backup_fixture(tmp_path)
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "manage.sh"),
            "prune",
            "--keep",
            "2",
            "--instance",
            str(instance),
            "--output",
            str(backups),
            "--confirm",
            f"{project}:prune:2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    remaining = sorted(item.name for item in backups.iterdir())
    assert remaining == [
        f"{project}-20260104T000000Z-4",
        f"{project}-20260105T000000Z-5",
        "other-20260101T000000Z-9",
    ]


def test_prune_requires_matching_confirmation(tmp_path):
    instance, backups, project = _backup_fixture(tmp_path)
    before = sorted(item.name for item in backups.iterdir())
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "manage.sh"),
            "prune",
            "--keep",
            "1",
            "--instance",
            str(instance),
            "--output",
            str(backups),
            "--confirm",
            f"{project}:prune:9",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert sorted(item.name for item in backups.iterdir()) == before


def test_plan_prune_is_read_only(tmp_path):
    instance, backups, project = _backup_fixture(tmp_path)
    before = sorted(item.name for item in backups.iterdir())
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "manage.sh"),
            "plan",
            "prune",
            "--keep",
            "2",
            "--instance",
            str(instance),
            "--output",
            str(backups),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert f"{project}:prune:2" in result.stdout
    assert sorted(item.name for item in backups.iterdir()) == before


def test_instances_reports_uninitialized_state_dir(tmp_path):
    runtime = tmp_path / "absent-runtime"
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "instances"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_RUNTIME_DIR": str(runtime)},
    )
    assert result.returncode == 0, result.stderr
    assert "状态目录尚未初始化" in result.stdout
    assert "install" in result.stdout


def test_status_reports_uninitialized_state_dir(tmp_path):
    runtime = tmp_path / "absent-runtime"
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "status"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_RUNTIME_DIR": str(runtime)},
    )
    assert result.returncode == 2
    assert "状态目录尚未初始化" in result.stderr
    assert "install" in result.stderr


def test_instances_excludes_backup_snapshots(tmp_path):
    instance, backups, project = _backup_fixture(tmp_path)
    (backups / f"{project}-20260105T000000Z-5/.backup-snapshot").write_text(f"{project}\n")
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "instances"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_MANAGE_RUNTIME_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert str(instance) in result.stdout
    assert str(backups / f"{project}-20260105T000000Z-5") not in result.stdout


def test_backup_snapshot_is_rejected_as_instance(tmp_path):
    _instance, backups, project = _backup_fixture(tmp_path)
    snapshot = backups / f"{project}-20260105T000000Z-5"
    (snapshot / ".backup-snapshot").write_text(f"{project}\n")
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "check", "--instance", str(snapshot)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "备份快照" in result.stderr


def test_preflight_reports_environment_without_network():
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), "preflight"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "OFFICE_PREFLIGHT_OFFLINE": "1"},
    )
    assert result.returncode in (0, 1)
    assert "部署预检" in result.stdout
    assert "Docker引擎" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["purge"],
        ["delete-data"],
        ["uninstall"],
        ["down", "--volumes"],
        ["down", "--rmi", "all"],
    ],
)
def test_management_refuses_data_and_image_deletion(arguments):
    result = subprocess.run(
        ["bash", str(ROOT / "manage.sh"), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "拒绝" in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="Bash为服务器入口")
def test_missing_docker_explains_prerequisite(tmp_path):
    # 只提供脚本本身依赖的dirname，不让测试读取宿主Docker。
    tool = tmp_path / "dirname"
    tool.write_text('#!/bin/sh\ncase "$1" in */*) echo "${1%/*}";; *) echo .;; esac\n')
    tool.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "deploy/install.sh")],
        env={**os.environ, "PATH": str(tmp_path)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "缺少Docker" in result.stderr and "docker compose version" in result.stderr


def test_client_missing_address_requires_operator_not_local_ip(tmp_path, monkeypatch):
    setup = client(monkeypatch)
    with pytest.raises(ValueError, match="不能使用员工电脑IP"):
        setup.ensure_connection(tmp_path, interactive=False)
    answers = iter(["http://192.168.1.8:8800", "yes"])
    setup.ensure_connection(tmp_path, interactive=True, input_fn=lambda prompt: next(answers))
    assert "192.168.1.8" in (tmp_path / ".env").read_text()
    setup.ensure_connection(tmp_path, interactive=False)


def test_client_does_not_change_installed_identity_or_follow_symlinks(tmp_path, monkeypatch):
    setup = client(monkeypatch)
    (tmp_path / ".office").mkdir()
    (tmp_path / ".office/source.json").write_text("{}")
    with pytest.raises(ValueError, match="新连接"):
        setup.ensure_connection(tmp_path, interactive=True)


def test_invalid_manual_address_does_not_replace_config(tmp_path, monkeypatch):
    setup = client(monkeypatch)
    original = "OFFICE_PUBLIC_URL=https://office.example\nOFFICE_DEPLOYMENT_MODE=https\n"
    (tmp_path / ".env").write_text(original)
    answers = iter(["http://8.8.8.8:8800", "yes"])
    with pytest.raises(ValueError, match="私有"):
        setup.ensure_connection(tmp_path, interactive=True, input_fn=lambda prompt: next(answers))
    assert (tmp_path / ".env").read_text() == original
    assert not list(tmp_path.glob("tmp*"))
