"""发布版本与来源门禁；不创建远程标签，不触发发布。"""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "release_test", ROOT / "scripts/prepare_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    generated = json.loads((ROOT / "template-state.json").read_text())["generated_files"]
    paths = [
        "template.toml",
        "template-state.json",
        *generated,
        "VERSION",
        "pyproject.toml",
        "uv.lock",
        "contracts/openapi.json",
        "releases/v1.0.0.md",
        "office-data-service/pyproject.toml",
        "clients/python/pyproject.toml",
        "packages/api-contracts/pyproject.toml",
    ]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return tmp_path


def test_release_requires_matching_version_tag_and_packages(release, source):
    assert release.validate_versions(source, "v1.0.0") == "1.0.0"
    with pytest.raises(ValueError, match="标签"):
        release.validate_versions(source, "v1.0.1")
    project = source / "clients/python/pyproject.toml"
    project.write_text(project.read_text().replace('version = "1.0.0"', 'version = "1.0.1"'))
    with pytest.raises(ValueError, match="工作区版本"):
        release.validate_versions(source, "v1.0.0")


def test_release_rejects_stale_lock_and_contract(release, source):
    lock = source / "uv.lock"
    original = lock.read_text()
    lock.write_text(
        original.replace(
            'name = "office-client"\nversion = "1.0.0"', 'name = "office-client"\nversion = "0.1.0"'
        )
    )
    with pytest.raises(ValueError, match="锁文件"):
        release.validate_versions(source)
    lock.write_text(original)
    schema = source / "contracts/openapi.json"
    content = json.loads(schema.read_text())
    content["info"]["version"] = "0.1.0"
    schema.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="OpenAPI"):
        release.validate_versions(source)


def test_release_will_not_overwrite_existing_artifacts(release, source):
    output = source / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("existing")
    with pytest.raises(ValueError, match="输出已存在"):
        release.build(source, output, "v1.0.0")
    assert marker.read_text() == "existing"
