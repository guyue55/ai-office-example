"""再次认证ID令牌必须验证签名、受众、用途和nonce；不能当业务令牌使用。"""

import time
from types import SimpleNamespace

import jwt
import pytest
from app.common.errors import DomainError
from app.infrastructure.config import Settings
from app.modules.access.authentication import TokenVerifier
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"nonce": "another"},
        {"nonce": "不同来源"},
        {"typ": "Bearer"},
        {"aud": "another"},
        {"azp": "another"},
        {"exp": 1},
    ],
)
def test_id_token_has_separate_validation_purpose(tmp_path, change):
    settings = Settings(
        database_user="unused",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid/realms/office",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    verifier = TokenVerifier(settings)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier.jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda raw: SimpleNamespace(key=key.public_key())
    )
    claims = {
        "iss": settings.issuer,
        "aud": settings.web_client_id,
        "sub": "operator",
        "typ": "ID",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "nonce": "browser-nonce",
        "auth_time": int(time.time()),
        **change,
    }
    token = jwt.encode(claims, key, algorithm="RS256")
    if not change:
        assert verifier.verify_id(token, "browser-nonce").subject == "operator"
    else:
        with pytest.raises(DomainError) as caught:
            verifier.verify_id(token, "browser-nonce")
        assert caught.value.status == 401
    with pytest.raises(DomainError):
        verifier.verify(token)
