"""任务持久化及归属；只负责数据与状态，不在事务内等待模型或真人。"""

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import load_only

from app.common.errors import require
from app.infrastructure.models import AgentRun
from app.modules.agent.state import ACTIVE, run_view


class AgentRecords:
    def __init__(self, access):
        self.access, self.db = access, access.db
        access.need("agent.run")

    async def owned(self, identifier, *, lock=False):
        query = select(AgentRun).where(
            AgentRun.id == identifier, AgentRun.owner_id == self.access.actor.id
        )
        row = await self.db.scalar(query.with_for_update() if lock else query)
        require(row is not None, 404, "not_found", "任务不存在或不属于当前员工")
        return row

    async def create(
        self, prompt, request_key, digest, *, per_user, total, conversation_id=None, context_page=""
    ):
        previous = await self.db.scalar(
            select(AgentRun).where(
                AgentRun.owner_id == self.access.actor.id, AgentRun.request_key == request_key
            )
        )
        if previous is not None:
            require(
                previous.request_digest == digest,
                409,
                "constraint_conflict",
                "同一请求标识的任务内容或配置已经变化",
            )
            return previous, False
        if conversation_id:
            # 局部导入避免模块环；同会话锁与全局权限锁一起保证受理串行。
            from app.modules.agent.conversations import Conversations

            await Conversations(self.access).attach(conversation_id)
        active = select(AgentRun).where(AgentRun.status.in_(ACTIVE))
        own = await self.db.scalar(
            select(func.count()).select_from(
                active.where(AgentRun.owner_id == self.access.actor.id).subquery()
            )
        )
        all_count = await self.db.scalar(select(func.count()).select_from(active.subquery()))
        require(
            own < per_user and all_count < total,
            429,
            "agent_capacity",
            "活动任务已达上限，请等待或停止原任务",
        )
        row = AgentRun(
            owner_id=self.access.actor.id,
            conversation_id=conversation_id,
            context_page=context_page,
            access_fingerprint=await self.access.authorization_fingerprint()
            if conversation_id
            else None,
            prompt=prompt,
            request_key=request_key,
            request_digest=digest,
            status="queued",
            version=1,
            trace_id=self.access.trace_id,
        )
        self.db.add(row)
        await self.db.flush()
        self.access.audit("agent.created", row.id, {"status": "queued"})
        return row, True

    async def listing(self, *, after="", limit=10, page=None, q="", status="", since="", until=""):
        query = select(AgentRun).where(AgentRun.owner_id == self.access.actor.id)
        if q:
            query = query.where(AgentRun.prompt.contains(q, autoescape=True))
        if status:
            query = query.where(AgentRun.status == status)
        if since:
            query = query.where(AgentRun.created_at >= datetime.fromisoformat(since))
        if until:
            query = query.where(AgentRun.created_at < datetime.fromisoformat(until))
        total = await self.db.scalar(select(func.count()).select_from(query.subquery()))
        if after:
            anchor = await self.owned(after)
            query = query.where(
                or_(
                    AgentRun.created_at < anchor.created_at,
                    (AgentRun.created_at == anchor.created_at) & (AgentRun.id < anchor.id),
                )
            )
        offset = ((page or 1) - 1) * limit
        query = query.options(
            load_only(
                AgentRun.id,
                AgentRun.version,
                AgentRun.prompt,
                AgentRun.status,
                AgentRun.cancel_requested,
                AgentRun.created_at,
                AgentRun.updated_at,
                AgentRun.conversation_id,
                AgentRun.access_fingerprint,
            )
        )
        rows = list(
            await self.db.scalars(
                query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        )
        more = len(rows) > limit
        rows = rows[:limit]
        pagination = None
        if not after:
            pagination = {
                "page": page or 1,
                "page_size": limit,
                "total": total,
                "pages": max(1, (total + limit - 1) // limit),
                "start": offset + 1 if rows else 0,
                "end": offset + len(rows) if rows else 0,
            }
        items = []
        fingerprint = await self.access.authorization_fingerprint()
        for row in rows:
            item = run_view(row, detail=False)
            if row.conversation_id and row.access_fingerprint != fingerprint:
                item["prompt"] = "权限配置已变化，此轮内容已隐藏。"
            items.append(item)
        return {
            "items": items,
            "has_more": more,
            "next": rows[-1].id if more else None,
            "pagination": pagination,
        }

    async def decide(self, identifier, version, proposal_id, approve):
        row = await self.owned(identifier, lock=True)
        require(
            row.version == version
            and row.status == "awaiting_confirmation"
            and row.pending
            and row.pending.get("ticket") == proposal_id
            and not row.cancel_requested,
            409,
            "constraint_conflict",
            "待批准操作已变化，请刷新核对",
        )
        # 只记录当前服务端提案的决定；调用者不能替换参数或模型历史。
        require(
            "decision" not in row.pending, 409, "constraint_conflict", "此提案已经决定，请刷新状态"
        )
        row.pending = {**row.pending, "decision": approve}
        row.version += 1
        row.updated_at = datetime.now(UTC)
        self.access.audit("agent.decided", row.id, {"approved": approve})
        return run_view(row)

    async def cancel(self, identifier, version):
        row = await self.owned(identifier, lock=True)
        require(row.version == version, 409, "constraint_conflict", "任务版本已变化，请刷新核对")
        if row.status in ACTIVE:
            row.cancel_requested = True
            row.version += 1
            row.updated_at = datetime.now(UTC)
            self.access.audit("agent.cancelled", row.id, {"requested": True})
        return run_view(row)
