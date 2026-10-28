"""模型管理输入、秘密后端及Web路由边界；不调用供应商。"""

from pathlib import Path

import pytest
from app.common.errors import DomainError
from app.infrastructure.model_secrets import cipher, decrypt, encrypt
from app.modules.agent.model_configuration import ModelDraft, ModelSave
from cryptography.fernet import Fernet
from pydantic import ValidationError
from test_web_console import web  # noqa: F401


@pytest.mark.parametrize(
    "url",
    [
        "https://x.invalid/v1/chat/completions",
        "https://x.invalid/responses",
        "https://x.invalid/v1/messages",
        "http://192.168.1.2/v1",
        "https://secret@x.invalid/v1",
        "https://x.invalid/v1?key=secret",
        "file:///tmp/x",
    ],
)
def test_reject_endpoint_mistakes(url):
    with pytest.raises(ValidationError):
        ModelDraft(model_url=url, model="fixture")


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
def test_protocol_draft_can_remain_unapproved(api):
    row = ModelDraft(api=api, model_url="https://model.invalid/v1", model="fixture")
    assert not row.approved_model_data


def test_secret_actions_and_redacted_representation():
    secret = "synthetic-secret-for-test"
    value = ModelSave(
        version=0,
        draft=ModelDraft(model_url="https://model.invalid/v1", model="fixture"),
        secret_action="replace",
        secret=secret,
    )
    assert secret not in repr(value) and secret not in value.model_dump_json()
    for action, raw in [("keep", secret), ("replace", None), ("clear", secret)]:
        with pytest.raises(ValidationError):
            ModelSave(version=0, draft=value.draft, secret_action=action, secret=raw)


def test_encrypted_secret_requires_original_restricted_key(tmp_path):
    key = tmp_path / "root"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    token = encrypt(key, "fixture-secret")
    assert "fixture-secret" not in token and decrypt(key, token) == "fixture-secret"
    key.write_bytes(Fernet.generate_key())
    with pytest.raises(DomainError):
        decrypt(key, token)
    key.chmod(0o644)
    with pytest.raises(DomainError):
        cipher(key)
    key.chmod(0o600)
    link = tmp_path / "symlink"
    link.symlink_to(key)
    with pytest.raises(DomainError):
        cipher(link)
    with pytest.raises(DomainError):
        cipher(Path("/nonexistent-model-test-key"))


def test_settings_page_has_no_existing_secret_and_csrf_guards_write(web):  # noqa: F811
    from app.application.commands import validate_command

    original = web.application.call.side_effect
    seen = []
    status = {
        "version": 0,
        "draft_revision": None,
        "active_revision": None,
        "source": "external",
        "installed": True,
        "ready": False,
        "secret_store_ready": True,
        "secret_set": False,
        "draft": None,
        "allowed_urls": ["https://model.invalid/v1"],
        "message": "",
    }

    async def operation(principal, name, payload, trace, **kwargs):
        if name == "me":
            return {
                "name": "管理员",
                "permissions": [
                    "agent.config.read",
                    "agent.config.manage",
                    "agent.config.apply",
                    "agent.config.test",
                ],
            }
        if name.startswith("agent.config."):
            validate_command(name, payload)
            seen.append((name, payload))
            return status
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    page = web.client.get("/console/model-settings")
    assert page.status_code == 200 and "智能助手配置" in page.text
    assert 'name="secret" type="password"' in page.text and "合成测试内容" not in page.text
    assert 'value="fixture-secret"' not in page.text
    assert (
        web.client.post("/console/model-settings/disable", data={"version": "0"}).status_code == 403
    )
    assert not any(name == "agent.config.disable" for name, _ in seen)
    result = web.client.post(
        "/console/model-settings/disable", data={"version": "0", "csrf": "form-csrf"}
    )
    assert result.status_code == 303 and seen[-1] == ("agent.config.disable", {"version": 0})


def test_managed_limits_match_existing_runtime_contract():
    pytest.importorskip("office_agent")
    from app.modules.agent.model_configuration import ModelLimits
    from office_agent.config import Limits

    # 防止无Agent依赖的管理契约与可选运行时预算随版本演进发生漂移。
    assert ModelLimits().model_dump() == Limits().model_dump()
    a, b = ModelLimits.model_json_schema()["properties"], Limits.model_json_schema()["properties"]
    assert set(a) == set(b)
    for key in a:
        for limit in ("minimum", "maximum", "type"):
            assert a[key].get(limit) == b[key].get(limit)


def test_model_settings_readonly_and_missing_prerequisites(web):  # noqa: F811
    async def operation(principal, name, payload, trace, **kwargs):
        if name == "me":
            return {"name": "配置观察员", "permissions": ["agent.config.read"]}
        assert name == "agent.config.get"
        return {
            "version": 0,
            "source": "external",
            "draft_revision": None,
            "active_revision": None,
            "draft": None,
            "active": None,
            "installed": False,
            "ready": False,
            "secret_set": False,
            "secret_store_ready": False,
            "allowed_urls": [],
            "message": "尚未配置",
        }

    web.application.call.side_effect = operation
    response = web.client.get("/console/model-settings")
    assert response.status_code == 200
    assert "您可以查看配置" in response.text and "需要运维准备" in response.text
    assert 'action="/console/model-settings/save"' not in response.text
    assert 'name="secret"' not in response.text
    assert "配置独立加密根密钥" in response.text
