"""目录无需模型ID，未保存探针不得通过MCP开放。"""

from app.application.commands import validate_command


def test_model_discovery_accepts_connection_without_model():
    args = validate_command(
        "agent.config.models",
        {
            "version": 0,
            "connection": {"model_url": "https://model.invalid/v1"},
            "secret_action": "clear",
        },
    )
    assert args["connection"]["model_url"] == "https://model.invalid/v1"


def test_unsaved_probe_accepts_current_draft_and_explicit_cost_confirmation():
    args = validate_command(
        "agent.config.probe",
        {
            "version": 0,
            "draft": {
                "model_url": "https://model.invalid/v1",
                "model": "fixture",
                "auth_mode": "none",
            },
            "acknowledge_cost": True,
            "request_key": "synthetic-probe-1",
        },
    )
    assert args["request_key"] == "synthetic-probe-1"


def test_probe_requires_cost_ack_and_is_not_an_agent_tool():
    import pytest
    from app.entrypoints.mcp.validation import TOOL_INPUTS
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        validate_command(
            "agent.config.probe",
            {
                "version": 0,
                "draft": {"model_url": "https://model.invalid/v1", "model": "fixture"},
                "request_key": "once",
            },
        )
    assert not any("configuration" in name or "model_probe" in name for name in TOOL_INPUTS)
