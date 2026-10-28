"""托管配置到既有异步模型运行时的桥；普通服务不静态依赖Agent SDK。"""

import asyncio
import time
from contextlib import asynccontextmanager
from importlib import import_module

from app.application.agent_runner import AgentRunner
from app.common.errors import DomainError, require
from app.infrastructure import model_secrets
from app.infrastructure.model_egress import check_endpoint, model_transport


async def prepare_runtime(application, revision, *, model_context=None, synthetic=False):
    settings = application.settings
    require(
        synthetic or revision.config.get("approved_model_data") is True,
        422,
        "invalid_input",
        "启用前需确认模型数据使用许可",
    )
    check_endpoint(settings, revision.config["model_url"])
    require(revision.secret_ciphertext is not None, 422, "invalid_input", "请先设置模型密钥")
    try:
        config = import_module("office_agent.config").AgentConfig.model_validate(
            {
                **revision.config,
                # 只为无工具合成探针构建临时配置，不持久化或改变启用许可。
                "approved_model_data": True
                if synthetic
                else revision.config["approved_model_data"],
                "service_url": settings.public_url,
                "allow_lan_http_demo": settings.allow_lan_http_demo,
            }
        )
        ciphertext = revision.secret_ciphertext
        # 应用前验证秘密可解密；不向环境变量写入，不创建明文临时文件。
        await asyncio.to_thread(model_secrets.decrypt, settings.agent_secret_key_file, ciphertext)

        @asynccontextmanager
        async def managed_model(bound_config):
            key = await asyncio.to_thread(
                model_secrets.decrypt, settings.agent_secret_key_file, ciphertext
            )
            async with import_module("office_agent.providers").configured_model(
                bound_config,
                key=key,
                transport=model_transport(
                    settings,
                    bound_config.model_url,
                    max_response_bytes=65536 if synthetic else None,
                ),
            ) as model:
                yield model

        return config, AgentRunner(
            application, config, model_context=model_context or managed_model
        )
    except DomainError:
        raise
    except Exception:  # noqa: BLE001 -- SDK／配置异常可能带输入及秘密，不返回原异常。
        raise DomainError(
            503, "execution_unavailable", "模型配置无法应用，请核对运行依赖、数据许可和预算"
        ) from None


async def test_runtime(config, runner):
    """只有固定合成输入，不挂企业工具；不返回供应商文本或错误原文。"""
    started = time.monotonic()
    try:
        Agent = import_module("pydantic_ai").Agent
        UsageLimits = import_module("pydantic_ai.usage").UsageLimits
        async with asyncio.timeout(
            min(
                config.request_timeout,
                max(1, runner.application.settings.operation_timeout - 3),
                30,
            )
        ):
            async with runner.model_context(config) as model:
                await Agent(model, retries=0).run(
                    "仅回复OK。这是连接测试，不需要调用任何工具。",
                    model_settings={"max_tokens": 32},
                    usage_limits=UsageLimits(request_limit=1, total_tokens_limit=512),
                )
        return {
            "passed": True,
            "category": "ok",
            "message": "基础合成调用通过；不代表业务工具、余额或全部模型能力可用",
            "suggestion": "核对数据使用许可后，可显式启用此修订。",
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 -- 超时／第三方正文不得进入业务响应及日志。
        from app.application.model_test_errors import failure

        return {**failure(exc), "elapsed_ms": round((time.monotonic() - started) * 1000)}
