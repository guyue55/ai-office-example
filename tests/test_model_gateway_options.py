"""模型网关参数与局域网接入的服务端契约。"""

import pytest
from app.infrastructure.config import Settings
from app.modules.agent.model_configuration import ModelDraft
from pydantic import ValidationError


def test_private_http_can_be_declared_and_explicitly_approved(tmp_path):
    url = "http://192.168.1.20:11434/v1"
    assert ModelDraft(model_url=url, model="local").model_url == url
    settings = Settings(
        database_user="test",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
        agent_model_urls=[url],
    )
    assert settings.agent_model_urls == [url]


def test_gateway_options_are_structured_and_optional_auth_is_explicit():
    draft = ModelDraft(
        model_url="https://model.invalid/v1",
        model="fixture",
        auth_mode="none",
        extra_headers={"x-route": "blue"},
        extra_query={"api-version": "2026-01-01"},
    )
    assert draft.auth_mode == "none"


@pytest.mark.parametrize(
    "options",
    [
        {"extra_headers": {"Authorization": "secret"}},
        {"extra_headers": {"Host": "other.invalid"}},
        {"extra_headers": {"x-api-key": "secret"}},
        {"extra_headers": {"x-route": "bad\r\nvalue"}},
        {"extra_query": {"access_token": "secret"}},
        {"auth_mode": "custom-header", "auth_header": "Host"},
    ],
)
def test_gateway_rejects_secret_or_transport_overrides(options):
    with pytest.raises(ValidationError):
        ModelDraft(model_url="https://model.invalid/v1", model="fixture", **options)


def test_managed_no_auth_revision_does_not_require_or_decrypt_secret(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.application.model_runtime import prepare_runtime

    def forbidden(*args):
        raise AssertionError("无密钥模式不能访问秘密后端")

    monkeypatch.setattr("app.infrastructure.model_secrets.decrypt", forbidden)
    settings = Settings(
        database_user="test",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    application = SimpleNamespace(settings=settings, sessions=None)
    revision = SimpleNamespace(
        config=ModelDraft(
            model_url="https://model.invalid/v1",
            model="fixture",
            auth_mode="none",
            approved_model_data=True,
        ).model_dump(),
        secret_ciphertext=None,
    )

    async def scenario():
        config, runner = await prepare_runtime(application, revision)
        assert config.auth_mode == "none"
        async with runner.model_context(config):
            pass  # 构造上下文不发请求，也不读取宿主环境凭证。

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "options",
    [
        {"auth_mode": "none", "extra_query": {"api-version": "2026-01-01"}},
        {
            "auth_mode": "custom-header",
            "auth_header": "x-gateway-key",
            "extra_headers": {"x-route": "blue"},
        },
        {"extra_headers": {"Host": "bad"}},
        {"extra_query": {"access_token": "secret"}},
        {"extra_headers": {"X-Route": "a", "x-route": "b"}},
        {"extra_headers": {"x-route": "bad\r\nvalue"}},
    ],
)
def test_managed_and_employee_gateway_validation_agree(options):
    from office_agent.config import AgentConfig

    outcomes = []
    for model, base in [
        (ModelDraft, {"model_url": "https://model.invalid/v1", "model": "fixture"}),
        (
            AgentConfig,
            {
                "service_url": "https://service.invalid",
                "model_url": "https://model.invalid/v1",
                "model": "fixture",
                "approved_model_data": True,
            },
        ),
    ]:
        try:
            model.model_validate({**base, **options})
            outcomes.append(True)
        except ValidationError:
            outcomes.append(False)
    assert outcomes[0] == outcomes[1]


def test_public_http_model_is_refused_even_when_explicitly_listed():
    from types import SimpleNamespace

    from app.common.errors import DomainError
    from app.infrastructure.model_egress import check_endpoint

    url = "http://8.8.8.8/v1"
    with pytest.raises(DomainError):
        check_endpoint(
            SimpleNamespace(agent_model_url_policy="public", agent_model_urls=[url]), url
        )
