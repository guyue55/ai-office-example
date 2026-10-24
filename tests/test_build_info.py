"""内容身份的真实变更检测；不以包版本相同判定已应用。"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from office_client import build_info

ROOT = Path(__file__).resolve().parents[1]


def checker():
    spec = importlib.util.spec_from_file_location(
        "test_build_checker", ROOT / "scripts/check_build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_content_identity_is_order_independent_and_content_sensitive():
    first = [("module/a.py", b"a"), ("module/b.json", b"{}")]
    assert build_info.content_identity(first) == build_info.content_identity(reversed(first))
    assert build_info.content_identity(first) != build_info.content_identity(
        [("module/a.py", b"b")]
    )


def test_installed_content_reads_actual_bytes_and_ignores_bytecode(monkeypatch, tmp_path):
    root = tmp_path / "office_client"
    root.mkdir()
    (root / "a.py").write_bytes(b"a")
    monkeypatch.setattr(
        build_info.importlib.util,
        "find_spec",
        lambda _: SimpleNamespace(submodule_search_locations=[str(root)]),
    )
    first = build_info.installed_identity("office-client")
    (root / "__pycache__").mkdir()
    (root / "__pycache__/a.pyc").write_bytes(b"runtime")
    assert build_info.installed_identity("office-client") == first
    (root / "a.py").write_bytes(b"changed")
    assert build_info.installed_identity("office-client") != first


def test_verifier_rejects_changed_installed_content():
    module = checker()
    manifest = {"artifacts": [{"name": "office-client", "content_sha256": "wrong"}]}
    assert module.verify_installed(manifest, ["office-client"]) == {"office-client": False}


def test_record_links_exact_source_and_rejects_stale_wheels(monkeypatch, tmp_path):
    import zipfile

    import pytest

    module = checker()
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda args, **_: b"a" * 40 if "rev-parse" in args else b"",
    )
    (tmp_path / "uv.lock").write_bytes(b"lock")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    for name in ("openapi.json", "business-codes.json"):
        (contracts / name).write_bytes(b"{}")
    directory = tmp_path / "dist"
    directory.mkdir()
    for name, package in module.PACKAGES.items():
        source = tmp_path / module.SOURCES[name]
        source.mkdir(parents=True)
        (source / "__init__.py").write_bytes(b"code")
        with zipfile.ZipFile(directory / (name + ".whl"), "w") as archive:
            archive.writestr(package + "/__init__.py", b"code")
            archive.writestr(name + ".dist-info/METADATA", f"Name: {name}\nVersion: 0.1.0\n")
    manifest = module.record(tmp_path, directory)
    assert manifest["source_commit"] == "a" * 40 and len(manifest["artifacts"]) == 3
    module.verify_artifacts(manifest, directory)
    first = directory / manifest["artifacts"][0]["file"]
    before = first.read_bytes()
    first.write_bytes(before + b"tampered")
    with pytest.raises(ValueError, match="哈希"):
        module.verify_artifacts(manifest, directory)
    first.write_bytes(before)
    (tmp_path / module.SOURCES["office-client"] / "__init__.py").write_bytes(b"new code")
    with pytest.raises(ValueError, match="不一致"):
        module.record(tmp_path, directory)
