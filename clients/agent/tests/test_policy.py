"""敏感信息清洗回归：守住「键名变体不漏、文本内联凭证不漏、预算计数不误伤」。"""

import pytest

from office_agent.policy import is_secret_key, sanitize, sanitize_text


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "db_password",
        "temporary_password",
        "apiKey",
        "api_key",
        "X-Api-Key",
        "token",
        "session_token",
        "access_token",
        "secret",
        "client_secret",
        "private_key",
        "cookie",
        "credentials",
        "code_verifier",
    ],
)
def test_secret_key_variants_are_recognized(key):
    assert is_secret_key(key) is True
    assert sanitize({key: "值"}) == {key: "[已隐藏]"}


@pytest.mark.parametrize(
    "key",
    [
        "max_tokens",
        "token_limit",
        "input_tokens",
        "output_tokens",
        "token_count",
        "key_id",
        "key_name",
        "name",
    ],
)
def test_budget_counters_are_not_redacted(key):
    """用量统计不是秘密；误伤会让会话成本信息失真。"""
    assert is_secret_key(key) is False
    assert sanitize({key: 1024}) == {key: 1024}


def test_text_credentials_are_redacted():
    """文本里的内联凭证同样不得外发。"""
    assert sanitize_text("Authorization: Bearer abc.def.ghi") == "Authorization: Bearer [已隐藏]"
    assert sanitize_text("Basic dXNlcjpwYXNz") == "Basic [已隐藏]"
    assert (
        sanitize_text("https://x.test/a?access_token=abc123&b=1")
        == "https://x.test/a?access_token=[已隐藏]&b=1"
    )
    assert "sk-abcdefgh12345" not in sanitize_text("key sk-abcdefgh12345")
    assert "AKIAIOSFODNN7EXAMPLE" not in sanitize_text("id AKIAIOSFODNN7EXAMPLE")
    assert "ghp_abcdefgh1234" not in sanitize_text("id ghp_abcdefgh1234")
    assert (
        sanitize_text("-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----")
        == "[已隐藏私钥]"
    )


def test_nested_structures_are_cleaned():
    value = {"user": {"password": "x", "name": "张三"}, "items": [{"apiKey": "y"}, {"count": 2}]}
    assert sanitize(value) == {
        "user": {"password": "[已隐藏]", "name": "张三"},
        "items": [{"apiKey": "[已隐藏]"}, {"count": 2}],
    }
