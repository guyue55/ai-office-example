"""标准访问令牌校验；令牌只证明身份，不固化业务角色。"""

import asyncio
import hmac
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKClient

from app.common.async_work import WorkCapacityExceeded, bounded_thread
from app.common.errors import DomainError
from app.infrastructure.config import Settings
from app.infrastructure.secrets import read_secret
from app.modules.access.session_errors import SessionEnded


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    claims: dict


class TokenVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._capacity = asyncio.Semaphore(settings.auth_workers)
        self._online_capacity = asyncio.Semaphore(settings.auth_workers)
        self._secret_capacity = asyncio.Semaphore(settings.auth_workers)
        self.http = (
            httpx.AsyncClient(timeout=httpx.Timeout(5, connect=2), follow_redirects=False)
            if settings.token_introspection
            else None
        )
        self.jwks = PyJWKClient(
            settings.issuer.rstrip("/") + "/protocol/openid-connect/certs",
            cache_keys=False,
            lifespan=300,
            timeout=5,
        )

    async def verify_async(self, token: str) -> Principal:
        principal = await self._async(self.verify, token)
        if self.http is not None:
            await self._online(token)
        return principal

    async def aclose(self):
        if self.http is not None:
            await self.http.aclose()

    async def _online(self, token: str):
        config = self.settings.token_introspection
        assert config is not None and self.http is not None
        if self._online_capacity.locked():
            raise DomainError(503, "authentication_busy", "身份校验繁忙，请稍后重试")
        async with self._online_capacity:
            try:
                async with asyncio.timeout(5):
                    value = await read_secret(config.client_secret_file, self._secret_capacity)
                    response = await self.http.post(
                        self.settings.issuer + "/protocol/openid-connect/token/introspect",
                        data={
                            "token": token,
                            "token_type_hint": "access_token",
                            "client_id": config.client_id,
                            "client_secret": value,
                        },
                    )
                if response.status_code != 200:
                    raise DomainError(503, "identity_unavailable", "在线身份校验暂不可用")
                payload = response.json()
                if not isinstance(payload, dict) or type(payload.get("active")) is not bool:
                    raise ValueError("在线身份响应无效")
                if not payload["active"]:
                    raise SessionEnded()
            except WorkCapacityExceeded:
                raise DomainError(503, "authentication_busy", "身份校验繁忙，请稍后重试") from None
            except (httpx.HTTPError, OSError, ValueError, TimeoutError):
                raise DomainError(503, "identity_unavailable", "在线身份校验暂不可用") from None

    async def verify_id_async(self, token: str, nonce: str) -> Principal:
        return await self._async(lambda raw: self.verify_id(raw, nonce), token)

    async def _async(self, verify, token: str) -> Principal:
        """不排队积压；调用方取消后，名额仍保留到真实线程结束。"""
        try:
            return await bounded_thread(self._capacity, verify, token)
        except WorkCapacityExceeded:
            raise DomainError(503, "authentication_busy", "身份校验繁忙，请稍后重试") from None

    def verify(self, token: str) -> Principal:
        try:
            key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.settings.issuer,
                audience=self.settings.audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud", "sid", "azp"]},
                leeway=5,
            )
            scope = claims.get("scope")
            if (
                any(
                    type(claims.get(key)) is not int or not 0 <= claims[key] <= 253402300799
                    for key in ("iat", "exp")
                )
                or (
                    "auth_time" in claims
                    and (
                        type(claims["auth_time"]) is not int
                        or not 0 <= claims["auth_time"] <= 253402300799
                    )
                )
                or not isinstance(claims.get("sid"), str)
                or not 0 < len(claims["sid"]) <= 200
                or "\x00" in claims["sid"]
                or not isinstance(claims.get("azp"), str)
                or not 0 < len(claims["azp"]) <= 200
                or "\x00" in claims["azp"]
                or claims.get("typ") != "Bearer"
                or not isinstance(scope, str)
                or "office" not in scope.split()
            ):
                raise jwt.InvalidTokenError("需要访问令牌")
            return Principal(claims["iss"], claims["sub"], claims)
        except jwt.PyJWKClientConnectionError:
            raise DomainError(
                503, "identity_unavailable", "身份服务暂时不可用，请稍后重试"
            ) from None
        except (jwt.PyJWTError, ValueError):
            raise DomainError(401, "invalid_token", "访问凭证无效或已过期") from None

    def verify_id(self, token: str, nonce: str) -> Principal:
        """再次认证使用独立的ID令牌用途，不能冒充业务访问令牌。"""
        try:
            key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.settings.issuer,
                audience=self.settings.web_client_id,
                options={"require": ["exp", "iat", "sub", "iss", "aud", "nonce", "auth_time"]},
                leeway=5,
            )
            if (
                claims.get("typ") != "ID"
                or not isinstance(claims["nonce"], str)
                or not hmac.compare_digest(claims["nonce"].encode(), nonce.encode())
            ):
                raise jwt.InvalidTokenError("认证用途不匹配")
            if claims.get("azp", self.settings.web_client_id) != self.settings.web_client_id:
                raise jwt.InvalidTokenError("认证客户端不匹配")
            return Principal(claims["iss"], claims["sub"], claims)
        except jwt.PyJWKClientConnectionError:
            raise DomainError(503, "identity_unavailable", "身份服务暂时不可用") from None
        except (jwt.PyJWTError, ValueError):
            raise DomainError(401, "invalid_token", "再次认证未通过，请重新验证") from None
