"""套件检查与发布卫生；真实宿主行为不能由这些结构断言代替。"""

import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_contract_and_real_cli_examples():
    result = load("check_skills").check()
    assert result["skills"] == 2 and result["cli_examples"] == 9
    assert result["ai_behavior_verified"] is False


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    suite = tmp_path / "office-skills"
    suite.mkdir()
    (suite / "SKILL.md").write_text("candidate")
    (suite / "RULES.md").write_text("safe rules")
    commit(tmp_path)
    return tmp_path


def commit(root):
    subprocess.run(["git", "add", "office-skills"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
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


def test_package_integrity_determinism_and_runtime_exclusion(repo):
    tool = load("package_skills")
    # 未跟踪运行文件即使被忽略，也不得进入制品。
    (repo / ".gitignore").write_text("**/runs/\n")
    runtime = repo / "office-skills/runs"
    runtime.mkdir()
    (runtime / "private.json").write_text("synthetic private")
    first, second = repo / "a.zip", repo / "b.zip"
    tool.package(repo, first)
    tool.package(repo, second)
    assert first.read_bytes() == second.read_bytes()
    manifest = tool.verify_archive(first)
    assert not manifest["host_verified"]
    assert set(manifest["files"]) == {"office-skills/SKILL.md", "office-skills/RULES.md"}
    with zipfile.ZipFile(first) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["office-skills/RULES.md"] = b"tampered"
    with zipfile.ZipFile(second, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="摘要不匹配"):
        tool.verify_archive(second)


def test_package_rejects_dirty_source_and_secret_config(repo):
    tool = load("package_skills")
    (repo / "office-skills/SKILL.md").write_text("dirty")
    with pytest.raises(ValueError, match="未提交"):
        tool.package(repo, repo / "out.zip")
    config = repo / "office-skills/config"
    config.mkdir()
    (config / "source.yaml").write_text("access_token: synthetic-secret")
    commit(repo)
    with pytest.raises(ValueError, match="凭证字段"):
        tool.package(repo, repo / "out.zip")


def test_package_rejects_source_overwrite(repo):
    tool = load("package_skills")
    with pytest.raises(ValueError, match="源码"):
        tool.package(repo, repo / "office-skills/overwrite.zip")


def test_archive_rejects_undeclared_member(tmp_path):
    tool = load("package_skills")
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("office-skills/package-manifest.json", json.dumps({"files": {}}))
        archive.writestr("../outside", "invalid")
    with pytest.raises(ValueError, match="路径"):
        tool.verify_archive(path)
