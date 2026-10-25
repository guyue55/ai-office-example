"""发布边界负向回归：不允许额外资产、秘密归档、摘要或来源漂移。"""

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location(
        "release_verifier", ROOT / "scripts/verify_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seal(root, *, agent=False):
    artifacts = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.iterdir()
        if p.name not in {"release-manifest.json", "SHA256SUMS"}
    }
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "tag": "v1.0.0",
                "source_commit": "a" * 40,
                "artifacts": artifacts,
                **({"agent_runtime": True} if agent else {}),
            }
        )
    )
    (root / "SHA256SUMS").write_text(
        "".join(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
            for p in sorted(root.iterdir())
            if p.name != "SHA256SUMS"
        )
    )


@pytest.fixture
def candidate(tmp_path):
    for name in ("office_api_contracts", "office_data_service", "office_client"):
        with zipfile.ZipFile(tmp_path / f"{name}-1.0.0-py3-none-any.whl", "w") as z:
            z.writestr(f"{name}.dist-info/licenses/LICENSE", "test-license")
            z.writestr(f"{name}.dist-info/licenses/NOTICE", "test-notice")
        with tarfile.open(tmp_path / f"{name}-1.0.0.tar.gz", "w:gz") as t:
            entry = tarfile.TarInfo(f"{name}/README.md")
            entry.size = 4
            t.addfile(entry, io.BytesIO(b"test"))
    for name in ("example-source-1.0.0.zip", "example-client-1.0.0.zip", "office-skills.zip"):
        with zipfile.ZipFile(tmp_path / name, "w") as z:
            z.writestr("README.md", "test")
            if "-client-" in name:
                z.writestr(
                    ".env",
                    "OFFICE_PUBLIC_URL=https://office.example\nOFFICE_DEPLOYMENT_MODE=https\n",
                )
    (tmp_path / "build-manifest.json").write_text(json.dumps({"source_commit": "a" * 40}))
    for name in ("RELEASE_NOTES.md", "dependency-versions.json", "office-skills.zip.sha256"):
        (tmp_path / name).write_text("test")
    seal(tmp_path)
    return tmp_path


def test_exact_candidate_passes(verifier, candidate):
    assert verifier.verify(candidate, "v1.0.0", "a" * 40)["assets"] == 15


def test_agent_assets_require_explicit_marker_and_complete_pair(verifier, candidate):
    import shutil

    shutil.copyfile(
        candidate / "office_client-1.0.0-py3-none-any.whl",
        candidate / "office_agent-1.0.0-py3-none-any.whl",
    )
    shutil.copyfile(
        candidate / "office_client-1.0.0.tar.gz", candidate / "office_agent-1.0.0.tar.gz"
    )
    seal(candidate)
    with pytest.raises(ValueError, match="白名单"):
        verifier.verify(candidate)
    manifest = candidate / "release-manifest.json"
    data = json.loads(manifest.read_text())
    data["agent_runtime"] = True
    manifest.write_text(json.dumps(data))
    checksums = candidate / "SHA256SUMS"
    checksums.write_text(
        "".join(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
            for p in sorted(candidate.iterdir())
            if p.name != "SHA256SUMS"
        )
    )
    # 改名其他包、重算全部摘要并声明Agent仍不能通过内容核验。
    with pytest.raises(ValueError, match="缺少Agent运行模块"):
        verifier.verify(candidate)
    modules = {
        "office_agent/__init__.py": b'__version__="1.0.0"',
        "office_agent/runtime.py": b"# isolated fixture\n",
        "office_agent/py.typed": b"",
    }
    with zipfile.ZipFile(candidate / "example-source-1.0.0.zip", "a") as z:
        for name, content in modules.items():
            z.writestr("example-1.0.0/clients/agent/src/" + name, content)
    with pytest.raises(ValueError):
        verifier.verify_agent_content(candidate, "example-source-1.0.0.zip", "1.0.0")
    meta = b"Metadata-Version: 2.4\nName: office-agent\nVersion: 1.0.0\n"
    with zipfile.ZipFile(candidate / "office_agent-1.0.0-py3-none-any.whl", "w") as z:
        z.writestr("office_agent-1.0.0.dist-info/METADATA", meta)
        z.writestr("office_agent-1.0.0.dist-info/licenses/LICENSE", "test-license")
        z.writestr("office_agent-1.0.0.dist-info/licenses/NOTICE", "test-notice")
        for name, content in modules.items():
            z.writestr(name, content)
    with tarfile.open(candidate / "office_agent-1.0.0.tar.gz", "w:gz") as t:
        for name, content in {
            "PKG-INFO": meta,
            **{"src/" + n: c for n, c in modules.items()},
        }.items():
            member = tarfile.TarInfo("office_agent-1.0.0/" + name)
            member.size = len(content)
            t.addfile(member, io.BytesIO(content))
    seal(candidate, agent=True)
    assert verifier.verify(candidate)["assets"] == 17
    (candidate / "office_agent-1.0.0.tar.gz").unlink()
    with pytest.raises(ValueError):
        verifier.verify(candidate)


def test_extra_file_and_resealed_extra_file_are_rejected(verifier, candidate):
    (candidate / "private.txt").write_text("synthetic")
    with pytest.raises(ValueError, match="额外文件"):
        verifier.verify(candidate)
    seal(candidate)
    with pytest.raises(ValueError, match="白名单"):
        verifier.verify(candidate)


def test_tamper_and_commit_mismatch_are_rejected(verifier, candidate):
    with pytest.raises(ValueError, match="来源提交"):
        verifier.verify(candidate, commit="b" * 40)
    (candidate / "RELEASE_NOTES.md").write_text("changed")
    with pytest.raises(ValueError, match="摘要"):
        verifier.verify(candidate)


@pytest.mark.parametrize(
    "member",
    [
        "../escape",
        "/absolute",
        "C:\\escape",
        "runtime/run.json",
        "credentials/key",
        "deploy/.env",
        "key.pem",
        "__pycache__/cache.pyc",
    ],
)
def test_secret_paths_are_rejected(verifier, member):
    with pytest.raises(ValueError):
        verifier.safe_member(member)


def test_resealed_archive_with_secret_is_rejected(verifier, candidate):
    with zipfile.ZipFile(candidate / "example-source-1.0.0.zip", "a") as z:
        z.writestr("credentials/private.json", "synthetic")
    seal(candidate)
    with pytest.raises(ValueError, match="路径"):
        verifier.verify(candidate)
