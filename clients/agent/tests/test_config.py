"""不读取真实凭证；坏配置、秘密哨兵和缺少数据许可均应安全拒绝。"""

import json

import pytest
from pydantic import ValidationError

from office_agent.config import AgentConfig, Limits
from office_agent.policy import AgentError, sanitize


def configured(**kwargs):
    return {
        "service_url": "https://office.example",
        "model_url": "https://model.example/v1",
        "model": "some-tool-model",
        "api_key_env": "MODEL_KEY",
        "approved_model_data": True,
        **kwargs,
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"approved_model_data": False},
        {"service_url": "https://u:secret@office.example"},
        {"model_url": "http://192.168.1.2/v1", "allow_lan_http_demo": True},
        {"api_key_env": "sk-private-key"},
        {"limits": {"requests": 0}},
        {"limits": {"requests": True}},
        {"unknown": "secret"},
    ],
)
def test_config_refuses_unsafe_values(updates):
    with pytest.raises((ValueError, ValidationError)):
        AgentConfig.model_validate(configured(**updates))


def test_loopback_model_and_separate_http_policy():
    c = AgentConfig.model_validate(configured(model_url="http://127.0.0.1:1234/v1"))
    assert c.model_url.endswith("/v1")
    assert Limits().requests == 8


def test_file_error_does_not_echo_secrets(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('api_key_env = "top-secret-value"')
    with pytest.raises(AgentError) as error:
        AgentConfig.load(str(path))
    assert "top-secret-value" not in str(error.value)


def test_nested_secret_fields_and_bearer_are_hidden():
    value = sanitize(
        {"x": [{"access_token": "secret", "Set-Cookie": "secret2"}], "notice": "Bearer secret3"}
    )
    assert "secret" not in json.dumps(value)
