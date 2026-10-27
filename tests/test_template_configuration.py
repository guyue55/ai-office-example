"""配置与生成物一致性、命令注入拒绝及实例身份保护。"""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from template_fixtures import DEFAULT_TEMPLATE

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "template_configuration", ROOT / "scripts/configure_template.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    for name in [
        "VERSION",
        "contracts/openapi.json",
        "template.toml",
        "clients/python/pyproject.toml",
        "deploy/identity/session-policy.json",
    ]:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    (tmp_path / "template.toml").write_text(DEFAULT_TEMPLATE)
    return tmp_path


def test_two_brands_have_distinct_command_identity_and_consistent_resources(tool, project):
    config = project / "template.toml"
    original = config.read_text()
    for name, command, namespace in [
        ("甲方办公", "acme", "acme-office"),
        ("乙方平台", "beta", "beta-office"),
    ]:
        config.write_text(
            original.replace("企业智能办公平台", name)
            .replace('command = "office"', 'command = "' + command + '"')
            .replace('namespace = "office"', 'namespace = "' + namespace + '"')
        )
        tool.configure(project, config)
        tool.configure(project, config, check=True)
        data = [
            json.loads((project / p).read_text())
            for p in [
                "clients/python/src/office_client/product.json",
                "office-data-service/app/common/product.json",
                "deploy/installer/product.json",
                "installers/client/product.json",
            ]
        ]
        assert all(x == data[0] for x in data)
        assert data[0]["name"] == name and data[0]["command"] == command
        assert data[0]["client_id"] == namespace + "-cli"
        assert (
            command + ' = "office_client.cli:main"'
            in (project / "clients/python/pyproject.toml").read_text()
        )
        assert set(
            json.loads((project / "deploy/identity/session-policy.json").read_text())["clients"]
        ) == {namespace + "-cli", namespace + "-web"}
        assert tool.configure(project, config) == []


@pytest.mark.parametrize(
    "old,new",
    [
        ('command = "office"', 'command = "sh"'),
        ('namespace = "office"', 'namespace = "../oops"'),
        ("#2563EB", "red;display:none"),
        ('short_name = "智能办公"', 'short_name = "<script>"'),
        ("schema_version = 1", "schema_version = true"),
    ],
)
def test_invalid_configuration_never_writes(tool, project, old, new):
    config = project / "template.toml"
    config.write_text(config.read_text().replace(old, new))
    before = (project / "clients/python/pyproject.toml").read_bytes()
    with pytest.raises(ValueError):
        tool.configure(project, config)
    assert (project / "clients/python/pyproject.toml").read_bytes() == before
    assert not (project / "template-state.json").exists()


def test_live_instance_and_configuration_drift_are_blocked(tool, project):
    config = project / "template.toml"
    tool.configure(project, config)
    (project / "runtime").mkdir()
    (project / "runtime/instance-marker").write_text("synthetic")
    config.write_text(
        config.read_text().replace('namespace = "office"', 'namespace = "new-office"')
    )
    with pytest.raises(ValueError, match="漂移"):
        tool.configure(project, config, check=True)
    with pytest.raises(ValueError, match="实例"):
        tool.configure(project, config)
    assert (
        json.loads((project / "deploy/installer/product.json").read_text())["namespace"] == "office"
    )


def test_configuration_output_symlink_is_rejected(tool, project, tmp_path):
    original_version = (project / "VERSION").read_bytes()
    config = project / "template.toml"
    tool.configure(project, config)
    target = project / "deploy/installer/product.json"
    target.unlink()
    try:
        target.symlink_to(project / "VERSION")
    except OSError:
        pytest.skip("当前系统用户无符号链接创建权限")
    with pytest.raises(ValueError, match="软链"):
        tool.configure(project, config)
    assert (project / "VERSION").read_bytes() == original_version
