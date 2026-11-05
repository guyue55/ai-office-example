"""独立临时PG验证外部身份分页、确认与授权边界；不读取既有实例。"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.database import async_sessions
from app.infrastructure.models import Employee, IdentityLink, PendingIdentity
from sqlalchemy import select
from test_query_postgres import isolated_query_db, principal  # noqa: F401

pytestmark = pytest.mark.live


def test_pending_pagination_and_confirmed_external_binding(request):
    settings = request.getfixturevalue("isolated_query_db")

    async def scenario():
        factory = async_sessions(settings)
        application = Application(settings, factory, None)
        admin = principal(settings, "operator")

        async def call(operation, payload, who=admin):
            return await application.call(who, operation, payload, uuid4().hex)

        try:
            now = datetime.now(UTC)
            ids = [str(uuid4()) for _ in range(63)]
            async with factory.begin() as db:
                employee = Employee(name="隔离已开通人员", active=False)
                db.add(employee)
                await db.flush()
                db.add(
                    IdentityLink(employee_id=employee.id, issuer=settings.issuer, subject="linked")
                )
                db.add_all(
                    [
                        PendingIdentity(
                            id=identifier,
                            issuer=settings.issuer,
                            subject=f"test-{index}",
                            display_name="含%字面" if index == 0 else "合成访问者",
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                        for index, identifier in enumerate(ids)
                    ]
                )
                db.add_all(
                    [
                        PendingIdentity(
                            issuer=settings.issuer,
                            subject="linked",
                            display_name="停用也不重复入队",
                        ),
                        PendingIdentity(
                            issuer=settings.issuer + "/other",
                            subject="foreign",
                            display_name="其他来源",
                        ),
                        PendingIdentity(
                            issuer=settings.issuer,
                            subject="claimed",
                            display_name="已处理",
                            claimed_by=employee.id,
                        ),
                    ]
                )
            pages = [
                await call("access.identity.pending", {"page": page, "limit": 20})
                for page in range(1, 5)
            ]
            assert all(page["pagination"]["total"] == 63 for page in pages)
            assert [row["id"] for page in pages for row in page["items"]] == sorted(ids)
            assert (await call("access.identity.pending", {"page": 999, "limit": 20}))[
                "pagination"
            ]["page"] == 4
            literal = await call("access.identity.pending", {"q": "%"})
            assert literal["pagination"]["total"] == 1
            assert (await call("access.identity.pending", {"q": "no-match"}))["pagination"][
                "total"
            ] == 0
            for role_choice in (True, False):
                change = {
                    "operation": "access.identity.claim",
                    "payload": {
                        "pending_id": ids[int(role_choice)],
                        "name": "核对后开通",
                        "assign_default_role": role_choice,
                    },
                }
                preview = await call("access.change.preview", {"change": change})
                assert preview["before"]["issuer"] == settings.issuer
                assert preview["before"]["subject"] == f"test-{int(role_choice)}"
                assert bool(preview["after"].get("assigned_role")) is role_choice
                async with factory() as db:
                    assert (await db.get(PendingIdentity, ids[int(role_choice)])).claimed_by is None
                with pytest.raises(DomainError) as rejected:
                    await call(
                        "access.change.apply", {"change": change, "confirmation_digest": "0" * 64}
                    )
                assert rejected.value.status == 409
                result = await call(
                    "access.change.apply",
                    {"change": change, "confirmation_digest": preview["confirmation_digest"]},
                )
                assert bool(result.get("assigned_role")) is role_choice
                async with factory() as db:
                    link = await db.scalar(
                        select(IdentityLink).where(IdentityLink.employee_id == result["id"])
                    )
                    assert link.managed is False
                with pytest.raises(DomainError):
                    await call(
                        "access.change.apply",
                        {"change": change, "confirmation_digest": preview["confirmation_digest"]},
                    )
            assert (await call("access.identity.pending", {}))["pagination"]["total"] == 61
            with pytest.raises(DomainError) as denied:
                await call("access.identity.pending", {}, principal(settings, "test-0"))
            assert denied.value.status == 403
            # 手填路径同样保持非受管，且不会给没有绑定的登录者自动授予角色。
            subject = "manual-" + uuid4().hex
            change = {
                "operation": "access.create",
                "payload": {
                    "kind": "employees",
                    "body": {
                        "issuer": settings.issuer,
                        "subject": subject,
                        "name": "手填合成人员",
                        "assign_default_role": False,
                    },
                },
            }
            preview = await call("access.change.preview", {"change": change})
            result = await call(
                "access.change.apply",
                {"change": change, "confirmation_digest": preview["confirmation_digest"]},
            )
            async with factory() as db:
                link = await db.scalar(
                    select(IdentityLink).where(IdentityLink.employee_id == result["id"])
                )
                assert link.managed is False
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
