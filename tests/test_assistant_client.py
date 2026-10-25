"""服务器助手SDK及CLI薄适配，核对稳定请求标识和审批参数不被改写。"""

from unittest.mock import Mock

import pytest
from office_client import OfficeClient
from office_client.auth import ClientError
from office_client.cli import build_parser
from office_client.cli_assistant import execute


def test_hosted_sdk_does_not_retry_or_replace_decision():
    sdk = object.__new__(OfficeClient)
    sdk.request = Mock(return_value={"id": "run"})
    sdk.assistant_start("需求", "same-key")
    sdk.request.assert_called_once_with(
        "POST", "/api/v2/agent/runs", json={"prompt": "需求", "request_key": "same-key"}
    )
    sdk.assistant_decide("run", 3, "server-ticket", False)
    sdk.request.assert_called_with(
        "POST",
        "/api/v2/agent/runs/decision",
        json={"identifier": "run", "version": 3, "proposal_id": "server-ticket", "approve": False},
    )
    sdk.assistant_run("unsafe/identifier")
    sdk.request.assert_called_with("GET", "/api/v2/agent/runs/unsafe%2Fidentifier")


def test_hosted_cli_start_exact_object_and_explicit_decision(tmp_path):
    source = tmp_path / "task.json"
    source.write_text('{"prompt":"查看数据","request_key":"same-key"}')
    parser = build_parser()
    client = Mock()
    execute(client, parser.parse_args(["assistant-start", "--file", str(source)]))
    client.assistant_start.assert_called_once_with("查看数据", "same-key")
    source.write_text('{"prompt":"查看数据","request_key":"same-key","approve":true}')
    with pytest.raises(ClientError):
        execute(client, parser.parse_args(["assistant-start", "--file", str(source)]))
    execute(
        client,
        parser.parse_args(
            [
                "assistant-decide",
                "run",
                "--version",
                "2",
                "--proposal-id",
                "ticket",
                "--decision",
                "deny",
            ]
        ),
    )
    client.assistant_decide.assert_called_once_with("run", 2, "ticket", False)
