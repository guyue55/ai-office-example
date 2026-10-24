"""客户端跨平台文件与输出协议；构造数据，不连接真实身份或读取系统凭证。"""

import io
import json
import sys
from unittest.mock import Mock

import pytest
from office_client import cli, diagnostics, local_io
from office_client.auth import ClientError
from office_client.cli_commands import execute


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_cli_preview_chinese_file_and_path(monkeypatch, tmp_path, capsys, encoding):
    path = tmp_path / "中文 目录" / "提交 数据.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"project_id": "测试项目", "values": {"说明": "进度正常"}}, ensure_ascii=False),
        encoding=encoding,
    )
    # 模拟传统Windows默认编码：未声明编码的读取会走cp1252。
    original = type(path).read_text

    def read(target, *args, **kwargs):
        kwargs.setdefault("encoding", "cp1252")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", read)
    monkeypatch.setattr(cli, "OfficeClient", Mock(side_effect=AssertionError("不应访问网络")))
    monkeypatch.setattr(
        sys,
        "argv",
        ["office", "--service", "http://127.0.0.1:8800", "preview", "--file", str(path)],
    )
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["project_id"] == "测试项目" and result["field_ids"] == ["说明"]


@pytest.mark.parametrize("data", [b"\xff", b"not json", b"[]", b"null"])
def test_invalid_input_does_not_leak_file_content(tmp_path, data):
    path = tmp_path / "private.json"
    path.write_bytes(data)
    with pytest.raises(ClientError) as error:
        local_io.read_json_file(str(path))
    assert "private.json" not in str(error.value) and "not json" not in str(error.value)


def test_aggregate_reads_bom_before_calling_sdk(tmp_path):
    from types import SimpleNamespace

    path = tmp_path / "统计.json"
    query = {"dimensions": ["月份"]}
    path.write_text(json.dumps(query, ensure_ascii=False), encoding="utf-8-sig")
    client = Mock()
    args = SimpleNamespace(
        action="aggregate", file=str(path), identifier="p", revision=1, scope="own"
    )
    execute(client, args)
    client.aggregate.assert_called_once_with("p", 1, query, scope="own")


def test_windows_redirected_output_is_utf8(monkeypatch):
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    local_io.configure_cli_output()
    stream.write("信息✓")
    stream.flush()
    assert output.getvalue().decode("utf-8") == "信息✓"


def test_doctor_is_offline_and_does_not_read_credentials(monkeypatch, capsys):
    check = Mock()
    monkeypatch.setattr(diagnostics.KeyringStore, "check", check)
    monkeypatch.setattr(diagnostics.KeyringStore, "load", Mock(side_effect=AssertionError()))
    monkeypatch.setattr(cli, "OfficeClient", Mock(side_effect=AssertionError()))
    monkeypatch.setattr(sys, "argv", ["office", "doctor"])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    check.assert_called_once()
    assert result["ok"] is True
    assert result["credential_io_verified"] is False and result["online_verified"] is False


def test_doctor_fails_closed_without_secure_backend(monkeypatch, capsys):
    monkeypatch.setattr(
        diagnostics.KeyringStore, "check", Mock(side_effect=ClientError("unsupported"))
    )
    monkeypatch.setattr(sys, "argv", ["office", "doctor"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["checks"]["secure_backend_selected"] is False
