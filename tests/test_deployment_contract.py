"""容器入口与实例边界：本地默认不扩网，容器显式监听，运行与迁移密钥隔离。"""

import re
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from app.entrypoints import cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("extra,host", [([], "127.0.0.1"), (["--host", "0.0.0.0"], "0.0.0.0")])
def test_serve_keeps_local_default_and_requires_explicit_container_host(monkeypatch, extra, host):
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setattr(sys, "argv", ["office-service", "serve", *extra])
    monkeypatch.setattr(cli.Settings, "load", lambda: object())
    run = Mock()
    monkeypatch.setattr(cli.uvicorn, "run", run)
    cli.main()
    assert run.call_args.kwargs["host"] == host
    assert run.call_args.kwargs["proxy_headers"] is False
    assert run.call_args.kwargs["workers"] == 1
    assert run.call_args.kwargs["forwarded_allow_ips"] == ""


def test_invalid_port_fails_before_reading_secrets(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["office-service", "serve", "--port", "0"])
    load = Mock()
    monkeypatch.setattr(cli.Settings, "load", load)
    with pytest.raises(SystemExit):
        cli.main()
    load.assert_not_called()


def test_runtime_and_migration_mounts_are_separate():
    compose = yaml.safe_load((ROOT / "deploy/compose.service.yaml").read_text())
    service, migrate = (compose["services"][name] for name in ("service", "migrate"))
    assert service["volumes"][0]["source"].endswith("/service")
    assert migrate["volumes"][0]["source"].endswith("/maintenance")
    for component in (service, migrate):
        assert component["read_only"]
        assert component["cap_drop"] == ["ALL"]
        assert component["volumes"][0]["read_only"]
        assert component["security_opt"] == ["no-new-privileges:true"]
    assert migrate["profiles"] == ["maintenance"]
    assert service["ports"][0].startswith("127.0.0.1:")


def test_workflows_pin_actions_and_release_only_after_checks():
    for name in ("ci.yml", "image-release.yml"):
        workflow = yaml.load(
            (ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader
        )
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", step["uses"])
    release = yaml.load(
        (ROOT / ".github/workflows/image-release.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(release["on"]) == {"workflow_dispatch"}
    assert release["jobs"]["publish"]["needs"] == "checks"
    assert release["jobs"]["publish"]["environment"] == "image-release"
