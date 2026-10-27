"""人工模型设置入口；CSRF与应用权限共同约束，不向助手工具提供密钥。"""

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.common.errors import DomainError
from app.modules.agent.model_configuration import ModelLimits


def register_model_settings(router, operate, render, csrf, notify):
    @router.get("/console/model-settings")
    async def page(request: Request):
        status = await operate(request, "agent.config.get")
        return await render(
            request,
            "model_settings.html",
            configuration=status,
            limits=ModelLimits().model_dump(),
            draft=status["draft"] or {},
        )

    @router.post("/console/model-settings/{action}")
    async def change(request: Request, action: str):
        if action not in {"save", "apply", "disable", "test"}:
            raise DomainError(404, "not_found", "模型配置操作不存在")
        form = dict(await request.form())
        csrf(request, form)
        try:
            payload: dict = {"version": int(str(form.get("version", "")))}
            if action == "save":
                payload.update(
                    draft={
                        "api": form.get("api"),
                        "model": form.get("model"),
                        "model_url": form.get("model_url"),
                        "anthropic_auth": form.get("anthropic_auth", "bearer"),
                        "request_timeout": float(str(form.get("request_timeout", "30"))),
                        "approved_model_data": form.get("approved_model_data") == "on",
                        "limits": {
                            key: int(str(form.get("limit_" + key, default)))
                            for key, default in ModelLimits().model_dump().items()
                        },
                    },
                    secret_action=form.get("secret_action", "keep"),
                )
                if form.get("secret"):
                    payload["secret"] = form["secret"]
            elif action in {"apply", "test"}:
                payload["revision"] = int(str(form.get("revision", "")))
                if action == "test":
                    payload["acknowledge_cost"] = form.get("acknowledge_cost") == "on"
        except (TypeError, ValueError):
            raise DomainError(
                422, "invalid_input", "配置格式无效，请核对版本、超时与预算"
            ) from None
        result = await operate(request, "agent.config." + action, payload)
        messages = {
            "save": "草稿已保存，尚未启用，也未调用模型。",
            "apply": "配置已启用，新任务将使用当前生效修订。",
            "disable": "智能助手已停用，不会回退到外部文件配置。",
        }
        notify(request, result["message"] if action == "test" else messages[action])
        return RedirectResponse("/console/model-settings", status_code=303)
