"""托管配置到既有异步模型运行时的桥；普通服务不静态依赖Agent SDK。"""

import asyncio
from contextlib import asynccontextmanager
from importlib import import_module

from app.application.agent_runner import AgentRunner
from app.common.errors import DomainError, require
from app.infrastructure import model_secrets


async def prepare_runtime(application, revision, *, model_context=None):
    settings = application.settings
    require(
        revision.config.get("approved_model_data") is True,
        422,
        "invalid_input",
        "启用或测试前需确认模型数据使用许可",
    )
    require(
        revision.config["model_url"] in settings.agent_model_urls,
        422,
        "invalid_input",
        "该模型地址已不在允许出口中",
    )
    require(revision.secret_ciphertext is not None, 422, "invalid_input", "请先设置模型密钥")
    try:
        config = import_module("office_agent.config").AgentConfig.model_validate(
            {
                **revision.config,
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
                bound_config, key=key
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
        return True
    except Exception:  # noqa: BLE001 -- 超时／第三方正文不得进入业务响应及日志。
        return False
