"""会话输入/输出与身份边界：严格类型、时区、秘密隔离和原生会话归属。"""

import pytest
from app.application.commands import validate_command
from app.modules.access.session_schemas import SessionChange, SessionList, SessionView
from pydantic import ValidationError


@pytest.mark.parametrize(
    "payload",
    [
        {"limit": True},
        {"limit": "20"},
        {"limit": 0},
        {"archived": "false"},
        {"since": "2026-10-19"},
        {"since": "2026-02-30T00:00:00Z"},
        {"since": "2026-10-20T00:00:00Z", "until": "2026-10-19T00:00:00Z"},
        {"raw_sql": "select 1"},
    ],
)
def test_session_inputs_are_strict(payload):
    with pytest.raises(ValidationError):
        SessionList.model_validate(payload)


def test_session_defaults_and_unambiguous_dates():
    args = validate_command("session.list", {})
    assert args["limit"] == 20 and args["scope"] == "own"
    assert args == validate_command("session.list", {"limit": 20, "scope": "own"})
    assert SessionList(since="2026-10-19T00:00:00+08:00").since
    assert "session_id" not in SessionView.model_fields
    assert "refresh_token" not in SessionView.model_fields
    with pytest.raises(ValidationError):
        SessionChange(identifier="id", version=1, action="enable", reason="不允许复活")
    with pytest.raises(ValidationError):
        SessionChange(identifier="id", version=1, action="revoke", reason="")


def test_client_logout_does_not_repeat_completed_identity_cleanup():
    from types import SimpleNamespace
    from unittest.mock import Mock

    from office_client import OfficeClient

    client = object.__new__(OfficeClient)
    client.store = SimpleNamespace(load=Mock(return_value={"present": True}), clear=Mock())
    from office_client.credential_lock import credential_guard

    client.oauth = SimpleNamespace(
        logout=Mock(), coordinated=lambda: credential_guard(client.store)
    )
    client.request = Mock(return_value={"id": "current", "version": 1})
    client.change_authorization = Mock(return_value={"cleanup": "done"})
    client.logout()
    client.store.clear.assert_called_once()
    client.oauth.logout.assert_not_called()
