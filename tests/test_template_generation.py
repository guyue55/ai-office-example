"""新项目导出边界与来源可追溯；不复制运行状态或Git历史。"""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from template_fixtures import DEFAULT_TEMPLATE

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def generator(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "new_project_test", ROOT / "scripts/new_project.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commit(root):
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "test",
        ],
        cwd=root,
        check=True,
    )


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    for name in [
        "VERSION",
        "contracts/openapi.json",
        "template.toml",
        "clients/python/pyproject.toml",
        "deploy/identity/session-policy.json",
    ]:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    (source / "template.toml").write_text(DEFAULT_TEMPLATE)
    (source / ".gitignore").write_text("runtime/\n.env\n")
    commit(source)
    return source


def test_generated_project_uses_configuration_without_copying_history_or_runtime(
    generator, source, tmp_path
):
    runtime = source / "runtime"
    runtime.mkdir()
    (runtime / "private").write_text("synthetic-not-exported")
    (source / ".env").write_text("SYNTHETIC=not-exported")
    config = source / "template.toml"
    config.write_text(config.read_text().replace('command = "office"', 'command = "acme"'))
    target = tmp_path / "enterprise"
    result = generator.generate(source, target, config)
    assert result["template_commit"]
    assert (
        not (target / ".git").exists()
        and not (target / "runtime").exists()
        and not (target / ".env").exists()
    )
    assert json.loads((target / "installers/client/product.json").read_text())["command"] == "acme"
    assert (
        json.loads((target / "template-origin.json").read_text())["template_commit"]
        == result["template_commit"]
    )
    with pytest.raises(ValueError, match="覆盖"):
        generator.generate(source, target, config)


def test_dirty_program_and_tracked_secret_fail_before_output(generator, source, tmp_path):
    target = tmp_path / "enterprise"
    (source / "VERSION").write_text("2.0.0\n")
    with pytest.raises(ValueError, match="未提交"):
        generator.generate(source, target, source / "template.toml")
    assert not target.exists()
    subprocess.run(["git", "restore", "VERSION"], cwd=source, check=True)
    (source / "credentials").mkdir()
    (source / "credentials/private").write_text("synthetic-not-exported")
    commit(source)
    with pytest.raises(ValueError, match="不应导出"):
        generator.generate(source, target, source / "template.toml")
    assert not target.exists()
