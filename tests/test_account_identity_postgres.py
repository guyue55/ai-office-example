"""正式迁移库和真实身份端口联验；授权主体为合成对象，不冒充浏览器登录。"""

import asyncio
from uuid import uuid4

import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.config import IdentityManagement
from app.infrastructure.database import async_sessions
from app.infrastructure.models import Employee, IdentityLink
from sqlalchemy import select
from test_identity_admin_isolated import isolated_identity  # noqa: F401
from test_query_postgres import isolated_query_db, principal  # noqa: F401

pytestmark = pytest.mark.live


def test_real_account_create_link_and_revocation(isolated_query_db, isolated_identity):  # noqa: F811
    issuer, secret_file = isolated_identity
    original = isolated_query_db
    settings = original.model_copy(
        update={
            "issuer": issuer,
            "identity_management": IdentityManagement(
                client_id="management", client_secret_file=secret_file
            ),
        }
    )

    async def scenario():
        factory = async_sessions(settings)
        # 只重定向本轮新建测试库的合成管理员，不接触已有实例。
        async with factory.begin() as db:
            link = await db.scalar(
                select(IdentityLink).where(
                    IdentityLink.issuer == original.issuer, IdentityLink.subject == "operator"
                )
            )
            link.issuer = issuer
        application = Application(settings, factory, None)
        accounts = application.account_service()
        admin = principal(settings, "operator")

        async def call(operation, payload, who=admin):
            return await application.call(who, operation, payload, uuid4().hex)

        async def execute(command):
            preview = await call("account.preview", {"request": command})
            payload = {
                "request": preview["request"],
                "confirmation_digest": preview["confirmation_digest"],
                "idempotency_key": uuid4().hex,
            }
            receipt = await call("account.submit", payload)
            assert (await call("account.submit", payload))["id"] == receipt["id"]
            assert await accounts.runner.claim(receipt["id"]) == receipt["id"]
            await accounts.runner.step(receipt["id"])
            return await call("account.operation", {"identifier": receipt["id"]})

        try:
            username = "joined-" + uuid4().hex[:12]
            command = {"kind": "create", "name": "真实端口合成员工", "username": username}
            await call("account.preview", {"request": command})
            assert await accounts.provider.find_username(username) is None
            created = await execute(command)
            assert created["status"] == "awaiting_user"
            remote = await accounts.provider.find_username(username)
            assert remote is not None and not remote.enabled
            password = await accounts.runner.credential(
                created["id"], admin, uuid4().hex, created["version"]
            )
            assert isinstance(password, str) and len(password) >= 24
            del password
            with pytest.raises(DomainError) as replay:
                await accounts.runner.credential(
                    created["id"], admin, uuid4().hex, created["version"]
                )
            assert replay.value.status == 409
            assert await accounts.runner.claim(created["id"]) == created["id"]
            await accounts.runner.step(created["id"])
            completed = await call("account.operation", {"identifier": created["id"]})
            assert completed["status"] == "succeeded"
            assert (await accounts.provider.get(remote.id)).enabled
            person = principal(settings, remote.id)
            me = await call("me", {}, person)
            assert len(me["roles"]) == 1
            grants = await call(
                "access.links.get",
                {"kind": "employees", "identifier": completed["employee_id"], "relation": "roles"},
            )
            await call(
                "access.links.replace",
                {
                    "kind": "employees",
                    "identifier": completed["employee_id"],
                    "relation": "roles",
                    "body": {"version": grants["version"], "ids": []},
                },
            )
            for kind in ("disable", "enable"):
                detail = await call(
                    "access.detail", {"kind": "employees", "identifier": completed["employee_id"]}
                )
                result = await execute(
                    {
                        "kind": kind,
                        "identifier": completed["employee_id"],
                        "version": detail["version"],
                    }
                )
                assert result["status"] == "succeeded"
                assert (await accounts.provider.get(remote.id)).enabled == (kind == "enable")
            with pytest.raises(DomainError) as revoked:
                await call("me", {}, person)
            assert revoked.value.status == 401
            async with factory() as db:
                employee = await db.get(Employee, completed["employee_id"])
                person.claims["iat"] = employee.tokens_after + 1
            me = await call("me", {}, person)
            assert me["roles"] == [] and me["permissions"] == []
            external = await accounts.provider.create(
                "existing-" + uuid4().hex[:12], "合成既有账号", "", str(uuid4())
            )
            await accounts.provider.update(external.id, enabled=True)
            linked = await execute(
                {"kind": "link", "name": "合成关联人员", "remote_id": external.id}
            )
            assert linked["status"] == "succeeded"
            linked_me = await call("me", {}, principal(settings, external.id))
            assert len(linked_me["roles"]) == 1
        finally:
            await accounts.provider.aclose()
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
