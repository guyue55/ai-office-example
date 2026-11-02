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
        {"api": "guess-automatically"},
        {"api": "openai-completions", "anthropic_auth": "x-api-key"},
        {"api": "anthropic-messages", "anthropic_auth": "guess"},
        {"request_timeout": 0},
        {"request_timeout": 301},
        {"request_timeout": "30"},
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


def test_model_file_reference_is_relative_safe_and_takes_precedence(tmp_path, monkeypatch):
    from office_agent.providers import model_key

    secret = tmp_path / "model-key"
    secret.write_text("isolated-file-key")
    secret.chmod(0o600)
    source = tmp_path / "agent.toml"
    source.write_text(
        'service_url="https://office.example"\nmodel_url="https://model.example/v1"\n'
        'model="fixture"\napi_key_file="model-key"\napproved_model_data=true\n'
    )
    config = AgentConfig.load(str(source))
    monkeypatch.setenv("OFFICE_MODEL_API_KEY", "wrong-env-key")
    assert model_key(config) == "isolated-file-key"
    assert config.api_key_file == str(secret)
    secret.write_text("line1\nline2")
    with pytest.raises(AgentError):
        model_key(config)
    secret.unlink()
    with pytest.raises(AgentError):
        model_key(config)  # 指定文件失败不能回退到另一个模型凭证。


def test_model_key_file_rejects_world_readable_and_symlink(tmp_path):
    import os

    from office_agent.providers import model_key

    if os.name == "nt":
        pytest.skip("Windows ACL由部署者设置，POSIX位与O_NOFOLLOW仅在对应系统核对")
    secret = tmp_path / "key"
    secret.write_text("isolated-key")
    secret.chmod(0o644)
    config = AgentConfig.model_validate(configured(api_key_file=str(secret)))
    with pytest.raises(AgentError):
        model_key(config)
    secret.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(secret)
    config.api_key_file = str(link)
    with pytest.raises(AgentError):
        model_key(config)
