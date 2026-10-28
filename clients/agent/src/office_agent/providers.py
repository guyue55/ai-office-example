"""模型适配与生命周期；与企业OAuth完全分离，不建立外部trace出口。"""

import asyncio
import os
import stat
from contextlib import asynccontextmanager

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.openai import OpenAIProvider

from .config import AgentConfig
from .policy import AgentError


def model_key(config: AgentConfig) -> str:
    """明确文件引用优先；不回显秘密，不读取AI宿主凭证。"""
    if config.api_key_file is None:
        key = os.environ.get(config.api_key_env, "")
    else:
        try:
            fd = os.open(
                config.api_key_file,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_size > 8192
                    or (os.name != "nt" and info.st_mode & 0o077)
                ):
                    raise ValueError("秘密文件无效")
                key = stream.read(8193).strip()
        except (OSError, ValueError, UnicodeError):
            raise AgentError("模型密钥文件不可读取或权限不安全") from None
    if not key or len(key) > 8192 or "\n" in key or "\r" in key:
        raise AgentError("模型密钥引用未就绪；不会读取其他AI工具凭证")
    return key


@asynccontextmanager
async def configured_model(
    config: AgentConfig,
    *,
    key: str | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
):
    # 托管服务显式注入解密后的本修订密钥；独立员工仍只读原秘密引用。
    if key is None:
        key = await asyncio.to_thread(model_key, config)
    timeout = min(config.request_timeout, config.limits.seconds)

    async def isolate_headers(request: httpx2.Request):
        # SDK可读取宿主环境中的自定义头。固定协议只保留必要头，再绑定本配置凭证；
        # 不修改进程环境，避免并发任务串用其他AI工具的授权或组织信息。
        allowed = {
            "host",
            "accept",
            "accept-encoding",
            "connection",
            "content-length",
            "content-type",
            "user-agent",
            "anthropic-version",
            "anthropic-beta",
            "openai-beta",
            "idempotency-key",
        }
        for name in list(request.headers):
            if name.lower() not in allowed:
                del request.headers[name]
        if config.api == "anthropic-messages" and config.anthropic_auth == "x-api-key":
            request.headers["x-api-key"] = key
        else:
            request.headers["Authorization"] = "Bearer " + key

    async with httpx2.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=timeout,
        event_hooks={"request": [isolate_headers]},
    ) as http:
        if config.api == "anthropic-messages":
            # 可选运行时内按需加载；普通企业SDK不引入模型依赖。
            from anthropic import AsyncAnthropic
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider

            async with AsyncAnthropic(
                base_url=config.model_url,
                api_key=key,
                max_retries=0,
                timeout=timeout,
                http_client=http,
                open_telemetry={"traces": False, "propagation": False},
            ) as anthropic_client:
                yield AnthropicModel(
                    config.model, provider=AnthropicProvider(anthropic_client=anthropic_client)
                )
        else:
            async with AsyncOpenAI(
                base_url=config.model_url,
                api_key=key,
                organization="",
                project="",
                max_retries=0,
                timeout=timeout,
                http_client=http,
            ) as client:
                provider = OpenAIProvider(openai_client=client)
                if config.api == "openai-responses":
                    yield OpenAIResponsesModel(
                        config.model,
                        provider=provider,
                        settings=OpenAIResponsesModelSettings(openai_store=False),
                    )
                else:
                    yield OpenAIChatModel(config.model, provider=provider)
