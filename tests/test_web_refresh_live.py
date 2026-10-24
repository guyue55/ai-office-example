"""真实Keycloak刷新与业务撤销叠加；不延长令牌或修改身份服务配置。"""

import asyncio
from uuid import uuid4

import pytest
from app.application.service import Application
from app.entrypoints.web.authentication import WebAuthentication, bind_identity
from app.entrypoints.web.session import SessionStore
from app.infrastructure.database import async_sessions
from app.modules.access.authentication import TokenVerifier
from conftest import browser_login
from office_client.client import ServiceError
from office_client.product import PRODUCT


def test_real_refresh_retains_session_and_cannot_bypass_disable(live):
    client = browser_login("employee")
    other = browser_login("employee")
    current = client.describe_device("BFF续期验收", "独立浏览器")

    async def run():
        settings = live["settings"].model_copy(update={"web_client_id": PRODUCT["client_id"]})
        factory = async_sessions(settings)
        verifier = TokenVerifier(settings)
        application = Application(settings, factory, verifier)
        store = SessionStore(b"isolated-refresh-live-key", 3600)
        token = dict(client.store.load())
        original = await verifier.verify_async(token["access_token"])
        bind_identity(token, original)
        # 只推进本地续期标记；不篡改JWT、身份配置或服务端时间。
        token["_expires_at"] = 1
        cookie = store.create(token)
        deadline = next(iter(store.sessions.values()))[1]
        auth = WebAuthentication(application, store)
        try:
            principals = await asyncio.gather(*(auth.principal(cookie) for _ in range(3)))
            assert all(p.subject == original.subject for p in principals)
            assert all(p.claims["sid"] == original.claims["sid"] for p in principals)
            assert next(iter(store.sessions.values()))[1] == deadline
            result = await application.call(principals[0], "me", {}, str(uuid4()))
            assert result["id"] == live["employee_id"]
            return token["access_token"]
        finally:
            await verifier.aclose()
            await factory.kw["bind"].dispose()

    try:
        refreshed = asyncio.run(run())
        live["operator"].change_authorization(
            current["id"], current["version"], "disable", "续期后定点停用验证"
        )
        client.store.save({**client.store.load(), "access_token": refreshed})
        with pytest.raises(ServiceError) as caught:
            client.me()
        # SDK异常的结构化状态；禁止用异常正文匹配或回显令牌。
        assert getattr(caught.value, "status", None) == 401
        assert other.me()["id"] == live["employee_id"]
    finally:
        client.close()
        other.close()
