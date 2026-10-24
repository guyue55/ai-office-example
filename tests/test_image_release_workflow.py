"""运行真实Tag核验脚本：错误版本、恶意输入及不同提交不得进入镜像发布。"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tagged_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "VERSION").write_text("1.0.0\n")
    (tmp_path / "template.toml").write_text('[product]\nname="Example"\n')
    commit(tmp_path)
    subprocess.run(
        ["git", "tag", "-a", "v1.0.0", "-m", "test"], cwd=tmp_path, env=git_env(), check=True
    )
    return tmp_path


def git_env():
    return {
        **os.environ,
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
    }


def commit(root):
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "test"],
        cwd=root,
        env=git_env(),
        check=True,
    )


def execute(root, tag):
    workflow = yaml.load(
        (ROOT / ".github/workflows/image-release.yml").read_text(), Loader=yaml.BaseLoader
    )
    step = next(s for s in workflow["jobs"]["publish"]["steps"] if s.get("id") == "release")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    env = {
        **os.environ,
        "RELEASE_TAG": tag,
        "GITHUB_SHA": sha,
        "GITHUB_REPOSITORY": "Example/Template",
        "GITHUB_OUTPUT": str(root / "output"),
    }
    return subprocess.run(
        ["bash", "-c", step["run"]], cwd=root, env=env, capture_output=True, text=True, check=False
    )


def test_image_tag_must_match_version_and_main(tagged_repo):
    assert execute(tagged_repo, "v1.0.0").returncode == 0
    assert "image=ghcr.io/example/template/data-service" in (tagged_repo / "output").read_text()
    (tagged_repo / "updated").write_text("new change")
    commit(tagged_repo)
    assert execute(tagged_repo, "v1.0.0").returncode != 0


@pytest.mark.parametrize(
    "tag", ["v9.0.0", "--help", "v1.0.0; touch injected", "v1.0.0\ntouch injected"]
)
def test_bad_image_input_has_no_side_effects(tagged_repo, tag):
    assert execute(tagged_repo, tag).returncode != 0
    assert not (tagged_repo / "injected").exists()
    assert not (tagged_repo / "output").exists()
