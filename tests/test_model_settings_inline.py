"""同源临时探针与保存并启用边界，不读取真实身份。"""

from app.application.commands import validate_command
from app.common.errors import DomainError
from test_web_console import web  # noqa: F401


def test_inline_inspection_uses_contract_and_csrf(web):  # noqa: F811
    original = web.application.call.side_effect
    seen = []

    async def call(principal, operation, payload, trace, **kwargs):
        if operation == "agent.config.models":
            validate_command(operation, payload)
            seen.append(payload)
            return {
                "passed": True,
                "category": "ok",
                "message": "synthetic",
                "suggestion": "manual",
                "items": [{"id": "fixture", "name": "fixture"}],
                "truncated": False,
                "elapsed_ms": 1,
            }
        return await original(principal, operation, payload, trace)

    web.application.call.side_effect = call
    body = {
        "action": "models",
        "payload": {"version": 0, "connection": {"model_url": "https://model.invalid/v1"}},
    }
    assert web.client.post("/console/model-settings/inspect", json=body).status_code == 403
    result = web.client.post("/console/model-settings/inspect", json={**body, "csrf": "form-csrf"})
    assert result.status_code == 200 and result.json()["data"]["items"][0]["id"] == "fixture"
    assert result.headers["cache-control"] == "no-store"
    assert len(seen) == 1
    malformed = web.client.post(
        "/console/model-settings/inspect",
        content="not json",
        headers={"content-type": "application/json"},
    )
    assert malformed.status_code == 422 and "code" in malformed.json()


def test_save_and_apply_reports_partial_failure_without_retry(web):  # noqa: F811
    original = web.application.call.side_effect
    calls = []

    async def call(principal, operation, payload, trace, **kwargs):
        if operation == "me":
            return {"name": "管理员", "permissions": ["agent.config.manage", "agent.config.apply"]}
        if operation == "agent.config.save":
            validate_command(operation, payload)
            calls.append(operation)
            return {"version": 1, "draft_revision": 1}
        if operation == "agent.config.apply":
            calls.append(operation)
            raise DomainError(409, "version_conflict", "活动任务尚未结束")
        return await original(principal, operation, payload, trace)

    web.application.call.side_effect = call
    result = web.client.post(
        "/console/model-settings/save-apply",
        data={
            "csrf": "form-csrf",
            "version": "0",
            "api": "openai-completions",
            "model_url": "https://model.invalid/v1",
            "model": "fixture",
            "auth_mode": "none",
        },
    )
    assert result.status_code == 303
    assert calls == ["agent.config.save", "agent.config.apply"]
    assert "尚未启用" in web.store.load(web.cookie)["notice"]
