"""人工模型设置入口；CSRF与应用权限共同约束，不向助手工具提供密钥。"""

import json

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from office_contracts import Meta, prepare_success

from app.application.codebook import build_registry
from app.application.contracts import OPERATION_OUTPUTS
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
            budget_fields=ModelLimits.model_json_schema()["properties"],
            draft=status["draft"] or {},
        )

    @router.post("/console/model-settings/inspect")
    async def inspect_connection(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise DomainError(422, "invalid_input", "连接操作须为有效JSON") from None
        if not isinstance(body, dict) or body.get("action") not in {"models", "probe"}:
            raise DomainError(422, "invalid_input", "连接操作无效")
        csrf(request, body)
        operation = "agent.config." + body["action"]
        result = await operate(request, operation, body.get("payload", {}))
        prepared = prepare_success(
            result,
            OPERATION_OUTPUTS[operation],
            Meta(trace_id=request.state.trace_id),
            registry=build_registry(),
        )
        return Response(
            prepared.body, media_type="application/json", headers={"Cache-Control": "no-store"}
        )

    @router.post("/console/model-settings/{action}")
    async def change(request: Request, action: str):
        if action not in {
            "save",
            "save-test",
            "save-apply",
            "models",
            "probe",
            "apply",
            "disable",
            "test",
        }:
            raise DomainError(404, "not_found", "模型配置操作不存在")
        form = dict(await request.form())
        csrf(request, form)
        try:
            payload: dict = {"version": int(str(form.get("version", "")))}
            if action in {"save", "save-test", "save-apply", "models", "probe"}:
                payload.update(
                    draft={
                        "api": form.get("api"),
                        "model": form.get("model"),
                        "model_url": form.get("model_url"),
                        "anthropic_auth": form.get("anthropic_auth", "bearer"),
                        "auth_mode": form.get("auth_mode", "api-key"),
                        "auth_header": form.get("auth_header", "x-api-key"),
                        "extra_headers": json.loads(str(form.get("extra_headers", "{}")) or "{}"),
                        "extra_query": json.loads(str(form.get("extra_query", "{}")) or "{}"),
                        "request_timeout": float(str(form.get("request_timeout", "30"))),
                        "approved_model_data": form.get("approved_model_data") == "on",
                        "limits": {
                            key: int(str(form.get("limit_" + key, default)))
                            for key, default in ModelLimits().model_dump().items()
                        },
                    },
                    secret_action=form.get("secret_action", "keep"),
                    reuse_secret_at_new_url=form.get("reuse_secret_at_new_url") == "on",
                )
                if action == "models":
                    connection = payload.pop("draft")
                    for key in ("model", "limits", "approved_model_data"):
                        connection.pop(key)
                    payload["connection"] = connection
                elif action == "probe":
                    payload.update(
                        acknowledge_cost=True, request_key=str(form.get("request_key", ""))
                    )
                if form.get("secret"):
                    payload["secret"] = form["secret"]
            elif action in {"apply", "test"}:
                payload["revision"] = int(str(form.get("revision", "")))
                if action == "test":
                    payload["acknowledge_cost"] = form.get("acknowledge_cost") == "on"
        except (TypeError, ValueError):
            raise DomainError(
                422, "invalid_input", "配置格式无效，请核对版本、超时、预算及网关参数JSON"
            ) from None
        if action == "save-apply":
            me = await operate(request, "me")
            if "agent.config.apply" not in me["permissions"]:
                raise DomainError(403, "permission_denied", "缺少模型启用权限")
        if action == "save-test":
            # 先检查测试权限再保存，避免无权测试时意外保存；每阶段仍由应用重新授权。
            me = await operate(request, "me")
            if "agent.config.test" not in me["permissions"]:
                raise DomainError(403, "permission_denied", "缺少模型测试权限")
        result = await operate(
            request,
            "agent.config." + ("save" if action in {"save-test", "save-apply"} else action),
            payload,
        )
        if action in {"models", "probe"}:
            status = await operate(request, "agent.config.get")
            # 仅回填公开参数；密码由浏览器重新填写，绝不进入模板上下文。
            draft = payload.get("draft") or {
                **payload["connection"],
                "model": str(form.get("model", "")),
            }
            return await render(
                request,
                "model_settings.html",
                configuration=status,
                draft=draft,
                limits=ModelLimits().model_dump(),
                budget_fields=ModelLimits.model_json_schema()["properties"],
                inline_result=result,
            )
        if action == "save-apply":
            try:
                result = await operate(
                    request,
                    "agent.config.apply",
                    {"version": result["version"], "revision": result["draft_revision"]},
                )
            except DomainError:
                notify(
                    request,
                    "配置已保存，本次尚未启用。请核对当前生效配置、权限、活动任务及数据许可后再操作。",
                    error=True,
                )
                return RedirectResponse("/console/model-settings", status_code=303)
        if action == "save-test":
            try:
                result = await operate(
                    request,
                    "agent.config.test",
                    {
                        "version": result["version"],
                        "revision": result["draft_revision"],
                        "acknowledge_cost": True,
                    },
                )
            except DomainError:
                notify(
                    request,
                    "草稿已保存，但测试未完成。请核对当前修订及状态后再手动测试。",
                    error=True,
                )
                return RedirectResponse("/console/model-settings", status_code=303)
        messages = {
            "save": "配置已保存，尚未启用，也未调用模型。",
            "save-apply": "配置已保存并启用，新任务使用此配置。",
            "apply": "配置已启用，新任务将使用当前生效修订。",
            "disable": "智能助手已停用，不会回退到外部文件配置。",
        }
        notify(
            request,
            result["message"] if action in {"test", "save-test"} else messages[action],
            error=action in {"test", "save-test"} and not result["passed"],
        )
        return RedirectResponse("/console/model-settings", status_code=303)
