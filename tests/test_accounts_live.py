"""真实身份与PG的可恢复开户/安全操作验证，不替代浏览器首次设密验收。"""

import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.application.service import Application
from app.infrastructure.database import async_sessions
from app.infrastructure.models import IdentityLink
from sqlalchemy import select


def test_real_account_operation_chain(live):
    settings = live["settings"]
    operator_id = live["operator"].me()["id"]

    async def scenario():
        sessions = async_sessions(settings)
        app = Application(settings, sessions, None)
        accounts = app.account_service()
        try:
            async with sessions.begin() as db:
                subject = await db.scalar(
                    select(IdentityLink.subject).where(
                        IdentityLink.employee_id == operator_id,
                        IdentityLink.issuer == settings.issuer,
                    )
                )
            principal = SimpleNamespace(
                issuer=settings.issuer, subject=subject, claims={"iat": int(time.time())}
            )

            async def call(op, payload):
                return await app.call(principal, op, payload, str(uuid4()))

            async def drive(identifier):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    result = await call("account.operation", {"identifier": identifier})
                    if result["status"] not in ("pending", "running"):
                        return result
                    await asyncio.sleep(0.1)
                pytest.fail("真实后台执行器未在预算内完成账号步骤")

            command = {
                "kind": "create",
                "name": "真实开户验证",
                "username": "account-check-" + uuid4().hex[:10],
            }
            preview = await call("account.preview", {"request": command})
            submitted = {
                "request": preview["request"],
                "confirmation_digest": preview["confirmation_digest"],
                "idempotency_key": uuid4().hex,
            }
            op = await call("account.submit", submitted)
            assert (await call("account.submit", submitted))["id"] == op["id"]
            progress = await drive(op["id"])
            assert progress["status"] == "awaiting_user", progress["message"]
            password = await accounts.runner.credential(
                op["id"], principal, str(uuid4()), progress["version"]
            )
            assert len(password) > 24
            complete = await drive(op["id"])
            assert complete["status"] == "succeeded", complete["message"]
            person = await call("account.detail", {"identifier": complete["employee_id"]})
            assert person["active"] and person["account"]["enabled"]
            for kind in ("logout", "disable", "enable", "reset"):
                person = await call("account.detail", {"identifier": complete["employee_id"]})
                request = {
                    "kind": kind,
                    "identifier": person["employee_id"],
                    "version": person["version"],
                }
                preview = await call("account.preview", {"request": request})
                pending = await call(
                    "account.submit",
                    {
                        "request": preview["request"],
                        "confirmation_digest": preview["confirmation_digest"],
                        "idempotency_key": uuid4().hex,
                    },
                )
                progress = await drive(pending["id"])
                if kind == "reset":
                    assert progress["status"] == "awaiting_user", progress["message"]
                    await accounts.runner.credential(
                        pending["id"], principal, str(uuid4()), progress["version"]
                    )
                    progress = await drive(pending["id"])
                assert progress["status"] == "succeeded", progress["message"]
        finally:
            await accounts.runner.close()
            await sessions.kw["bind"].dispose()

    asyncio.run(scenario())
