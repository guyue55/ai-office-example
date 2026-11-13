"""本地边界和安全输出；不声称能识别全部个人信息或提示注入。"""

import json
import re


class AgentError(Exception):
    def __init__(self, message: str, *, outcome_unknown: bool = False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


class ToolFailure(AgentError):
    """适配器已分类的错误，不携带远端正文或凭证。"""


# 键名先做 camelCase/短横线归一（apiKey、X-Api-Key → api_key），再按后缀判定，
# 这样 db_password、session_token、private_key 这类常见变体不会被漏掉。
SECRET_KEYS = {
    "password",
    "passwd",
    "pwd",
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "bearer_token",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "secret_key",
    "private_key",
    "signing_key",
    "cookie",
    "set_cookie",
    "code_verifier",
    "credential",
    "credentials",
    "temporary_password",
    "initial_password",
}

# 只对键名做前缀/后缀识别有歧义，因此再补一组明确的子串规则：
# max_tokens、token_limit 这类预算上限不是秘密，必须排除，否则会误伤会话统计。
SECRET_KEY_SUFFIXES = ("_password", "_passwd", "_token", "_secret", "_key", "_credential")
SECRET_KEY_ALLOW = {
    "max_tokens",
    "token_limit",
    "input_tokens",
    "output_tokens",
    "token_count",
    "key_name",
    "key_id",
}


def normalized_key(key: str) -> str:
    """把 camelCase、短横线、空格统一成下划线形式，便于判定字段语义。"""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    return re.sub(r"[\s\-]+", "_", spaced).lower()


def is_secret_key(key: str) -> bool:
    """判断字段名是否指向秘密；预算类计数显式放行，避免误伤用量统计。"""
    name = normalized_key(key)
    if name in SECRET_KEY_ALLOW:
        return False
    return name in SECRET_KEYS or name.endswith(SECRET_KEY_SUFFIXES)


def sanitize(value):
    """阻断已知秘密字段，仍需企业批准模型目的地与数据范围。

    这只是本地兜底：不承诺识别全部个人信息或提示注入，摘要文本里的
    裸凭证若不符合已知形状仍可能漏出，因此外发仍须企业批准目的地与范围。
    """
    if isinstance(value, dict):
        return {
            key: "[已隐藏]" if is_secret_key(str(key)) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_text(value: str) -> str:
    """清洗文本内联凭证：Bearer/Basic 头、URL 查询参数、常见密钥前缀。"""
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [已隐藏]", value)
    text = re.sub(
        r"(?i)([?&;](?:access_token|refresh_token|id_token|token|api_key|apikey|secret|password|sig|signature)=)[^&#\s]+",
        r"\1[已隐藏]",
        text,
    )
    text = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,})\b",
        "[已隐藏]",
        text,
    )
    text = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        "[已隐藏私钥]",
        text,
    )
    return text


def encoded(value, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (ValueError, TypeError, RecursionError):
        raise AgentError("参数或结果不是有效的有界JSON") from None
    if len(text.encode()) > limit:
        raise AgentError("内容超过任务预算，请缩小查询或变更范围；未截断冒充完整结果")
    return text
