"""模型适配与生命周期；与企业OAuth完全分离，不建立外部trace出口。"""

import asyncio
import os
import stat
from contextlib import asynccontextmanager

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
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
async def configured_model(config: AgentConfig):
    key = await asyncio.to_thread(model_key, config)
    async with (
        httpx2.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as http,
        AsyncOpenAI(
            base_url=config.model_url, api_key=key, max_retries=0, timeout=30, http_client=http
        ) as client,
    ):
        yield OpenAIChatModel(config.model, provider=OpenAIProvider(openai_client=client))
