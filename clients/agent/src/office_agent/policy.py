"""本地边界和安全输出；不声称能识别全部个人信息或提示注入。"""

import json
import re


class AgentError(Exception):
    def __init__(self, message: str, *, outcome_unknown: bool = False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


class ToolFailure(AgentError):
    """适配器已分类的错误，不携带远端正文或凭证。"""


SECRET_KEYS = {
    "password",
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "api_key",
    "client_secret",
    "cookie",
    "set_cookie",
    "code_verifier",
}


def sanitize(value):
    """阻断已知秘密字段，仍需企业批准模型目的地与数据范围。"""
    if isinstance(value, dict):
        return {
            key: "[已隐藏]" if key.lower().replace("-", "_") in SECRET_KEYS else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [已隐藏]", value)
    return value


def encoded(value, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (ValueError, TypeError, RecursionError):
        raise AgentError("参数或结果不是有效的有界JSON") from None
    if len(text.encode()) > limit:
        raise AgentError("内容超过任务预算，请缩小查询或变更范围；未截断冒充完整结果")
    return text
