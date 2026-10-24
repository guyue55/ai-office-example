"""先提交业务撤销，再在事务外清理身份会话；故障不恢复旧访问权。"""

from sqlalchemy import select

from app.common.errors import DomainError
from app.infrastructure.models import AccessRevision, Audit, ClientAuthorization, IdentityLink
from app.modules.access.client_sessions import ClientSessions
from app.modules.access.service import Access


class Sessions:
    def __init__(self, sessions, provider):
        self.sessions, self.provider = sessions, provider

    async def request(self, principal, operation, payload, trace, finish):
        async with self.sessions.begin() as db:
            access = await Access.create(
                db, principal, trace, exclusive=operation != "session.list"
            )
            domain = ClientSessions(access)
            if operation == "session.list":
                return finish(await domain.listing(**payload))
            if operation == "session.register":
                return finish(await domain.register(**payload))
            row = await domain.change(**payload)
            result = await domain.view(row)
            # 在撤销事务内准备/校验输出；即使自己被撤销，也必须完成提交再返回。
            finish(result)
            row_id, version, actor_id = row.id, row.version, access.actor.id
            sid = row.session_id
            subject = await db.scalar(
                select(IdentityLink.subject).where(
                    IdentityLink.employee_id == row.employee_id, IdentityLink.issuer == row.issuer
                )
            )
        # 短事务已提交；外部失败只记清理失败，业务访问始终被拒绝。
        cleaned = False
        try:
            if self.provider is not None and subject:
                await self.provider.end_session(subject, sid)
                cleaned = True
        except DomainError:
            pass
        async with self.sessions.begin() as db:
            await db.scalar(select(AccessRevision).where(AccessRevision.id == 1).with_for_update())
            row = await db.get(ClientAuthorization, row_id)
            if row.version == version:
                row.cleanup = "done" if cleaned else "failed"
                db.add(
                    Audit(
                        actor_id=actor_id,
                        action="session.cleanup",
                        resource_id=row.id,
                        detail={"employee_name": result["employee_name"], "cleanup": row.cleanup},
                        trace_id=trace,
                    )
                )
                result["cleanup"] = row.cleanup
            else:
                # 并发后续操作优先；不能回写覆盖其状态。
                result["cleanup"] = "pending"
        return finish(result)
