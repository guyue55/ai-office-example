"""真实PG/OIDC链路：逐授权撤销，故障不放行，分页/审计/多入口一致。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.database import async_sessions, sessions
from app.infrastructure.models import Audit, ClientAuthorization
from app.modules.access.authentication import TokenVerifier
from conftest import browser_login
from live_environment import SERVICE_URL
from office_client import ServiceError
from sqlalchemy import select

BASE = SERVICE_URL + ""


def test_independent_session_revocation_and_archive(live):
    operator = live["operator"]
    first, second = browser_login("employee"), browser_login("employee")
    try:
        label = "授权验证-" + uuid4().hex[:10]
        row = first.describe_device(label, "macOS / 验证备注")
        other = second.describe_device(label + "-独立", "另一客户端")
        assert row["id"] != other["id"]
        raw = first.store.load().copy()
        assert row["current"] and row["state"] == "active"
        moment = datetime.fromisoformat(row["created_at"])
        window = first.client_authorizations(
            q=label,
            since=(moment - timedelta(seconds=1)).isoformat(),
            until=(moment + timedelta(seconds=1)).isoformat(),
        )
        assert any(item["id"] == row["id"] for item in window["items"])
        assert not first.client_authorizations(
            q=label, since=(moment + timedelta(days=1)).isoformat()
        )["items"]
        assert "session_id" not in row and "access_token" not in row
        with pytest.raises(ServiceError) as forbidden:
            first.client_authorizations(scope="all")
        assert forbidden.value.status == 403
        with pytest.raises(ServiceError) as forbidden:
            first.change_authorization(operator.describe_device()["id"], 1, "revoke", "越权验证")
        assert forbidden.value.status == 403
        rows = operator.client_authorizations(scope="all", q=label, limit=1)
        assert len(rows["items"]) == 1 and rows["has_more"] and rows["next"]
        page = operator.client_authorizations(scope="all", q=label, limit=1, after=rows["next"])
        assert len(page["items"]) == 1 and not page["has_more"]
        assert page["items"][0]["id"] != rows["items"][0]["id"]
        with pytest.raises(ServiceError) as invalid:
            operator.client_authorizations(scope="all", q="different", after=rows["next"])
        assert invalid.value.status == 422
        for params in (
            {"since": "2026-01-01"},
            {"since": "2026-02-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
            {"limit": 101},
            {"unknown": "bad"},
        ):
            with pytest.raises(ServiceError) as invalid:
                operator.client_authorizations(**params)
            assert invalid.value.status == 422
        result = operator.change_authorization(
            row["id"], row["version"], "disable", "指定设备停用验证"
        )
        assert result["state"] == "disabled" and result["cleanup"] == "done"
        assert second.me()["active"]
        for path in ("/api/v2/me", "/api/v2/projects", "/mcp"):
            response = httpx.get(
                BASE + path, headers={"Authorization": "Bearer " + raw["access_token"]}
            )
            assert response.status_code == 401
        config = first.oauth.config()
        refreshed = httpx.post(
            config["issuer"] + "/protocol/openid-connect/token",
            data={
                "grant_type": "refresh_token",
                "client_id": config["client_id"],
                "refresh_token": raw["refresh_token"],
            },
        )
        assert refreshed.status_code == 400
        with pytest.raises(ServiceError) as conflict:
            operator.change_authorization(row["id"], row["version"], "archive", "旧版本验证")
        assert conflict.value.status == 409
        result = operator.change_authorization(
            row["id"], result["version"], "archive", "保留审计归档验证"
        )
        assert result["archived_at"] and result["state"] == "revoked"
        assert all(
            item["id"] != row["id"]
            for item in operator.client_authorizations(scope="all", q=label)["items"]
        )
        archived = operator.client_authorizations(scope="all", q=label, archived=True)
        assert any(item["id"] == row["id"] for item in archived["items"])
        assert (
            httpx.get(
                BASE + "/api/v2/me", headers={"Authorization": "Bearer " + raw["access_token"]}
            ).status_code
            == 401
        )
        with sessions(live["settings"])() as db:
            audits = db.scalars(select(Audit).where(Audit.resource_id == row["id"])).all()
            assert {
                "session.created",
                "session.device",
                "session.disable",
                "session.archive",
                "session.cleanup",
            } <= {a.action for a in audits}
            assert all(
                "access_token" not in str(a.detail) and "refresh_token" not in str(a.detail)
                for a in audits
            )
        readable = operator.access_list("audit", q="授权验证", limit=100)
        # 审计默认查询支持人员/动作；本次对象的具体名称通过批量关联补全。
        all_audit = operator.access_list("audit", q=row["id"], limit=100)
        assert any(
            item["action_name"] == "归档客户端授权" and "客户端授权" in item["resource_name"]
            for item in all_audit["items"]
        )
        assert isinstance(readable["items"], list)
    finally:
        first.close()
        second.close()


def test_cleanup_outage_is_denied_then_retry(live):
    client = browser_login("employee")
    try:
        row = client.describe_device("故障清理验证-" + uuid4().hex[:6])
        token = client.store.load()["access_token"]
        settings = live["settings"]
        principal = TokenVerifier(settings).verify(live["operator"].oauth.access_token())

        class Outage:
            async def end_session(self, *args):
                raise DomainError(503, "identity_unavailable", "故障注入")

        async def scenario():
            factory = async_sessions(settings)
            app = Application(settings, factory, None)
            app._accounts = SimpleNamespace(provider=Outage())
            try:
                return await app.call(
                    principal,
                    "session.change",
                    {
                        "identifier": row["id"],
                        "version": row["version"],
                        "action": "revoke",
                        "reason": "外部故障验证",
                    },
                    str(uuid4()),
                )
            finally:
                await factory.kw["bind"].dispose()

        result = asyncio.run(scenario())
        assert result["cleanup"] == "failed" and result["state"] == "revoked"
        assert (
            httpx.get(BASE + "/api/v2/me", headers={"Authorization": "Bearer " + token}).status_code
            == 401
        )
        fixed = live["operator"].change_authorization(
            row["id"], result["version"], "retry", "恢复身份清理"
        )
        assert fixed["cleanup"] == "done"
    finally:
        client.close()


def test_parallel_first_use_and_self_revoke(live):
    client = browser_login("employee")
    try:
        token = client.store.load()["access_token"]

        def read(_):
            return httpx.get(
                BASE + "/api/v2/me", headers={"Authorization": "Bearer " + token}
            ).status_code

        with ThreadPoolExecutor(max_workers=6) as pool:
            assert list(pool.map(read, range(6))) == [200] * 6
        row = client.describe_device("并发首次登记")
        with sessions(live["settings"])() as db:
            assert (
                db.scalar(select(ClientAuthorization).where(ClientAuthorization.id == row["id"]))
                is not None
            )
            assert (
                len(
                    db.scalars(
                        select(Audit).where(
                            Audit.resource_id == row["id"], Audit.action == "session.created"
                        )
                    ).all()
                )
                == 1
            )
        result = client.change_authorization(
            row["id"], row["version"], "revoke", "本人结束本次授权"
        )
        assert result["current"] and result["cleanup"] == "done"
        assert read(0) == 401
    finally:
        client.close()


def test_sdk_logout_revokes_before_clearing_local_credentials(live):
    client = browser_login("employee")
    try:
        row = client.describe_device("客户端主动退出验证")
        old = client.store.load()["access_token"]
        client.logout()
        assert client.store.load() is None
        assert (
            httpx.get(BASE + "/api/v2/me", headers={"Authorization": "Bearer " + old}).status_code
            == 401
        )
        found = live["operator"].client_authorizations(scope="all", q=row["id"])["items"]
        assert len(found) == 1 and found[0]["state"] == "revoked" and found[0]["cleanup"] == "done"
    finally:
        client.close()
