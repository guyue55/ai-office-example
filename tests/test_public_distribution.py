"""公开分发的许可覆盖、包身份与第三方版本稳定性。"""

import json
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_owned_packages_ship_license_and_notice():
    license_text = (ROOT / "LICENSE").read_text()
    notice = (ROOT / "NOTICE").read_text()
    assert "Version 2.0, January 2004" in license_text
    for relative in ("clients/python", "office-data-service", "packages/api-contracts"):
        package = ROOT / relative
        project = tomllib.loads((package / "pyproject.toml").read_text())["project"]
        assert project["license"] == "Apache-2.0"
        assert set(project["license-files"]) == {"LICENSE", "NOTICE"}
        assert (package / "LICENSE").read_text() == license_text
        assert (package / "NOTICE").read_text() == notice
    for relative in ("office-skills", "installers/client"):
        assert (ROOT / relative / "LICENSE.md").read_text() == license_text
        assert (ROOT / relative / "NOTICE.md").read_text() == notice


def test_distribution_metadata_and_third_party_inventory_are_consistent():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {
        item["name"].lower().replace("_", "-").replace(".", "-"): item["version"]
        for item in lock["package"]
    }
    project = tomllib.loads((ROOT / "packages/api-contracts/pyproject.toml").read_text())
    assert project["project"]["name"] == "office-api-contracts"
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/office_contracts"
    ]
    assert (ROOT / "packages/api-contracts/src/office_contracts/__init__.py").is_file()
    inventory = json.loads((ROOT / "docs/dependency-licenses.json").read_text())
    for item in inventory["packages"]:
        name = item["name"].lower().replace("_", "-").replace(".", "-")
        assert item["version"] == locked[name]
        assert item["declared_license"] != "UNKNOWN"


def test_tracked_runtime_and_secret_paths_remain_excluded():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    forbidden = {"runtime", "credentials", "secrets", ".venv", "__pycache__"}
    for name in filter(None, names):
        path = Path(name)
        assert not (set(path.parts) & forbidden), name
        assert not (path.name.startswith(".env") and path.name != ".env.example"), name


def test_docker_context_keeps_contract_and_root_licenses():
    context = (ROOT / ".dockerignore").read_text()
    for name in (
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
        "packages/api-contracts/LICENSE",
        "packages/api-contracts/NOTICE",
    ):
        assert "!" + name in context
    dockerfile = (ROOT / "deploy/Dockerfile").read_text()
    assert "COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /opt/office/" in dockerfile
