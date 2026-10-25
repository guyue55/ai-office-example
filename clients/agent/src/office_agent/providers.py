"""模型适配与生命周期；与企业OAuth完全分离，不建立外部trace出口。"""

import os
from contextlib import asynccontextmanager

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import AgentConfig
from .policy import AgentError


@asynccontextmanager
async def configured_model(config: AgentConfig):
    key = os.environ.get(config.api_key_env, "")
    if not key:
        raise AgentError("未配置模型密钥引用对应的值；不会读取其他AI工具凭证")
    async with (
        httpx2.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as http,
        AsyncOpenAI(
            base_url=config.model_url, api_key=key, max_retries=0, timeout=30, http_client=http
        ) as client,
    ):
        yield OpenAIChatModel(config.model, provider=OpenAIProvider(openai_client=client))
