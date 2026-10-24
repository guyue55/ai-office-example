"""CLI参数透传与本地确认门；服务端授权由真实链路测试覆盖。"""

import json
import sys
from unittest.mock import MagicMock

import pytest
from office_client import cli
from office_client.cli_commands import file_digest


@pytest.fixture
def client(monkeypatch):
    obj = MagicMock()
    obj.__enter__.return_value = obj
    for method in (
        "projects",
        "records",
        "access_list",
        "client_authorizations",
        "account_history",
        "history",
        "confirm",
        "correct",
        "resume_account",
    ):
        getattr(obj, method).return_value = {"ok": True}
    monkeypatch.setattr(cli, "OfficeClient", lambda _: obj)
    return obj


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["office", "--service", "http://127.0.0.1:8800", *args])
    cli.main()


def test_paging_and_filters(monkeypatch, client):
    run(monkeypatch, "records", "--project-id", "p", "--after", "next", "--limit", "10")
    client.records.assert_called_once_with(project_id="p", after="next", limit=10)
    run(monkeypatch, "projects", "--after", "p", "--limit", "10")
    client.projects.assert_called_once_with(after="p", limit=10)
    run(
        monkeypatch,
        "access-list",
        "audit",
        "--actor",
        "张三",
        "--action",
        "session.created",
        "--page",
        "2",
    )
    assert client.access_list.call_args.kwargs["actor"] == "张三"
    assert client.access_list.call_args.kwargs["action"] == "session.created"
    assert client.access_list.call_args.kwargs["page"] == 2
    run(
        monkeypatch,
        "authorizations",
        "--page",
        "2",
        "--employee-id",
        "p",
        "--date-field",
        "last_seen_at",
    )
    assert client.client_authorizations.call_args.kwargs["employee_id"] == "p"
    run(monkeypatch, "account-history", "--page", "2", "--status", "needs_attention")
    assert client.account_history.call_args.kwargs["status"] == "needs_attention"


def test_lifecycle_confirmation_binds_file(monkeypatch, client, tmp_path):
    path = tmp_path / "confirm.json"
    payload = {"record_id": "r", "version": 2}
    path.write_text(json.dumps(payload))
    with pytest.raises(SystemExit):
        run(monkeypatch, "record-confirm", "--file", str(path), "--confirm-digest", "bad")
    client.confirm.assert_not_called()
    digest = file_digest("http://127.0.0.1:8800", payload)
    run(monkeypatch, "record-confirm", "--file", str(path), "--confirm-digest", digest)
    client.confirm.assert_called_once_with("r", 2)
    path.write_text(json.dumps({**payload, "version": 3}))
    with pytest.raises(SystemExit):
        run(monkeypatch, "record-confirm", "--file", str(path), "--confirm-digest", digest)
    assert client.confirm.call_count == 1


@pytest.mark.parametrize("default_encoding", ["cp1252", "gbk"])
def test_coverage_map_matches_registered_protocols(monkeypatch, default_encoding):
    import runpy
    from pathlib import Path

    # 在Mac/Linux也复现Windows默认编码，不能靠PYTHONUTF8=1掩盖漏项。
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        kwargs.setdefault("encoding", default_encoding)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    root = Path(__file__).resolve().parents[1]
    rows = runpy.run_path(str(root / "scripts/check_capability_coverage.py"))["check"]()
    assert rows["project.create"]["mode"] == "preview-confirm"
    assert rows["record.export.confirmed"]["cli"] == "export-confirmed"
