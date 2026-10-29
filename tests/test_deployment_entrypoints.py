"""独立部署入口：人工配置、不依赖Python宿主的服务器引导、客户端地址边界。"""

import importlib.util
import os
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
