"""BFF会话续期：令牌仅留服务端，同会话串行轮换，绝对会话寿命不延长。"""

import asyncio
import time

import httpx

from app.common.errors import DomainError
from app.entrypoints.web.session import LoginRequired


def bind_identity(token, principal):
    """只从已完成签名／在线验证的访问令牌登记身份锚点。"""
    claims = getattr(principal, "claims", {})
    if type(claims.get("exp")) is int:
        token["_identity"] = (principal.issuer, principal.subject, claims["sid"])
        token["_expires_at"] = claims["exp"]


class WebAuthentication:
    def __init__(self, application, store):
        self.application, self.store = application, store
        self.capacity = asyncio.Semaphore(application.settings.auth_workers)

    async def principal(self, cookie):
        try:
            return await self._principal(cookie)
        except TimeoutError:
            raise DomainError(503, "authentication_busy", "登录续期等待超时，请稍后重试") from None

    async def _principal(self, cookie):
        token = self.store.load(cookie)
        if (
            token.get("_identity")
            and token.get("refresh_token")
            and token["_expires_at"] <= time.time() + 15
        ):
            lock = token.setdefault("_refresh_lock", asyncio.Lock())
            # 等待者不占外部请求配额；锁只属于这个浏览器会话。
            async with asyncio.timeout(10), lock:
                if self.store.load(cookie) is not token:
                    raise LoginRequired("会话已变化，请重新登录")
                if token["_expires_at"] <= time.time() + 15:
                    if self.capacity.locked():
                        raise DomainError(503, "authentication_busy", "登录续期繁忙，请稍后重试")
                    async with self.capacity:
                        await self.refresh(cookie, token)
        return await self.application.verifier.verify_async(token["access_token"])

    async def refresh(self, cookie, token):
        settings = self.application.settings
        try:
            async with (
                asyncio.timeout(5),
                httpx.AsyncClient(timeout=5, follow_redirects=False) as client,
            ):
                response = await client.post(
                    settings.issuer + "/protocol/openid-connect/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": settings.web_client_id,
                        "refresh_token": token["refresh_token"],
                    },
                )
                if response.status_code in (400, 401):
                    raise DomainError(401, "login_required", "登录已过期，请重新登录")
                response.raise_for_status()
                payload = response.json()
                if (
                    not isinstance(payload, dict)
                    or not isinstance(payload.get("access_token"), str)
                    or not payload["access_token"]
                    or str(payload.get("token_type", "")).lower() != "bearer"
                    or (
                        "refresh_token" in payload
                        and (
                            not isinstance(payload["refresh_token"], str)
                            or not payload["refresh_token"]
                        )
                    )
                ):
                    raise ValueError("无效续期响应")
                principal = await self.application.verifier.verify_async(payload["access_token"])
                anchor = (principal.issuer, principal.subject, principal.claims["sid"])
                if anchor != token["_identity"] or principal.claims["exp"] <= time.time() + 15:
                    raise DomainError(401, "invalid_token", "续期身份或有效期异常，请重新登录")
                # 退出／重新认证／原TTL过期若发生在网络等待期间，不能复活旧会话。
                if self.store.load(cookie) is not token:
                    raise LoginRequired("会话已结束")
                token["access_token"] = payload["access_token"]
                token["refresh_token"] = payload.get("refresh_token", token["refresh_token"])
                token["_expires_at"] = principal.claims["exp"]
                if isinstance(payload.get("id_token"), str):
                    token["id_token"] = payload["id_token"]
                # csrf、recent_auth、credential_operation及SessionStore原截止时间全部保留。
        except asyncio.CancelledError:
            self.store.destroy(cookie)
            raise
        except (DomainError, LoginRequired):
            self.store.destroy(cookie)
            raise
        except (httpx.HTTPError, ValueError, TimeoutError):
            # 轮换结果可能已生效；不重用旧refresh_token，不自动重试写入或续期。
            self.store.destroy(cookie)
            raise DomainError(
                503, "identity_unavailable", "登录续期未完成，请重新打开控制台"
            ) from None
