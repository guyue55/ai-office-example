"""本人会话与有界轮次；服务器保存的任务是消息及执行结果的唯一来源。"""

import asyncio
from datetime import UTC, datetime

from office_contracts import Budget, ProtocolError, encode
from sqlalchemy import func, or_, select

from app.common.errors import require
from app.infrastructure.models import AgentConversation, AgentRun
from app.infrastructure.pagination import numbered_rows
from app.modules.agent.outputs import RunView
from app.modules.agent.state import ACTIVE, run_view

TURN_BUDGET = Budget(max_bytes=128000, max_nodes=4000, max_depth=24)


def _view_bytes(value):
    # 每页至多20轮：结构节点及深度预留整体统一响应的包装空间。
    try:
        return len(encode(value, RunView, budget=TURN_BUDGET))
    except ProtocolError:
        return TURN_BUDGET.max_bytes + 1


def conversation_view(row):
    return {
        key: getattr(row, key)
        for key in ("id", "title", "version", "archived", "created_at", "updated_at")
    }


class Conversations:
    def __init__(self, access):
        self.access, self.db = access, access.db
        access.need("agent.run")

    async def owned(self, identifier, *, lock=False):
        query = select(AgentConversation).where(
            AgentConversation.id == identifier, AgentConversation.owner_id == self.access.actor.id
        )
        row = await self.db.scalar(query.with_for_update() if lock else query)
        require(row is not None, 404, "not_found", "会话不存在或不属于当前员工")
        return row

    async def create(self, title):
        count = await self.db.scalar(
            select(func.count())
            .select_from(AgentConversation)
            .where(
                AgentConversation.owner_id == self.access.actor.id,
                AgentConversation.archived.is_(False),
            )
        )
        require(count < 100, 429, "agent_capacity", "未归档会话已达100个，请先归档不用的会话")
        row = AgentConversation(owner_id=self.access.actor.id, title=title)
        self.db.add(row)
        await self.db.flush()
        self.access.audit("agent.conversation.created", row.id, {})
        return conversation_view(row)

    async def listing(self, *, after="", limit=10, page=None, archived=False, q=""):
        query = select(AgentConversation).where(
            AgentConversation.owner_id == self.access.actor.id,
            AgentConversation.archived == archived,
        )
        if q:
            query = query.where(AgentConversation.title.icontains(q, autoescape=True))
        if page is not None:
            rows, pagination = await numbered_rows(
                self.db,
                query.order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc()),
                page,
                limit,
            )
            more = pagination["page"] < pagination["pages"]
            return {
                "items": [conversation_view(row) for row in rows],
                "has_more": more,
                "next": rows[-1].id if rows and more else None,
                "pagination": pagination,
            }
        if after:
            anchor = await self.owned(after)
            query = query.where(
                or_(
                    AgentConversation.updated_at < anchor.updated_at,
                    (AgentConversation.updated_at == anchor.updated_at)
                    & (AgentConversation.id < anchor.id),
                )
            )
        rows = list(
            await self.db.scalars(
                query.order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                .offset(((page or 1) - 1) * limit)
                .limit(limit + 1)
            )
        )
        return {
            "items": [conversation_view(row) for row in rows[:limit]],
            "has_more": len(rows) > limit,
            "next": rows[limit - 1].id if len(rows) > limit else None,
        }

    async def detail(self, identifier, *, before="", limit=10):
        row = await self.owned(identifier)
        query = select(AgentRun).where(
            AgentRun.conversation_id == row.id, AgentRun.owner_id == self.access.actor.id
        )
        if before:
            anchor = await self.db.get(AgentRun, before)
            require(
                anchor is not None and anchor.conversation_id == row.id,
                404,
                "not_found",
                "会话游标不存在",
            )
            query = query.where(
                or_(
                    AgentRun.created_at < anchor.created_at,
                    (AgentRun.created_at == anchor.created_at) & (AgentRun.id < anchor.id),
                )
            )
        fingerprint = await self.access.authorization_fingerprint()
        stream = await self.db.stream_scalars(
            query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(limit + 1)
            .execution_options(yield_per=1)
        )
        turns: list[dict] = []
        more, byte_count = False, 0
        try:
            async for turn in stream:
                if len(turns) >= limit:
                    more = True
                    break
                view = run_view(turn)
                if turn.access_fingerprint != fingerprint:
                    view.update(
                        prompt="权限配置已变化，此轮内容不再展示或用于续问。",
                        pending=None,
                        result=None,
                        history_hidden=True,
                    )
                # 大结果序列化移出事件循环；逐行读取，避免先加载多页JSON到内存。
                size = await asyncio.to_thread(_view_bytes, view)
                if size > 128000:
                    view.update(pending=None, result=None, detail_truncated=True)
                    size = await asyncio.to_thread(_view_bytes, view)
                if byte_count + size > 500000:
                    more = True
                    break
                turns.append(view)
                byte_count += size
        finally:
            await stream.close()
        turns.reverse()
        return {
            **conversation_view(row),
            "turns": turns,
            "has_more": more,
            "next": turns[0]["id"] if turns and more else None,
            "access_fingerprint": fingerprint,
            "history_reset": any(turn["history_hidden"] for turn in turns),
        }

    async def ensure_idle(self, identifier):
        row = await self.owned(identifier, lock=True)
        active = await self.db.scalar(
            select(AgentRun.id)
            .where(AgentRun.conversation_id == row.id, AgentRun.status.in_(ACTIVE))
            .limit(1)
        )
        require(active is None, 409, "constraint_conflict", "此会话仍有活动任务，请等待或先停止")
        return row

    async def attach(self, identifier):
        row = await self.ensure_idle(identifier)
        require(not row.archived, 409, "constraint_conflict", "会话已归档，请先恢复或新建会话")
        row.version += 1
        row.updated_at = datetime.now(UTC)
        return row

    async def archive(self, identifier, version, archived):
        row = await self.ensure_idle(identifier)
        require(row.version == version, 409, "constraint_conflict", "会话版本已变化，请刷新")
        if not archived and row.archived:
            count = await self.db.scalar(
                select(func.count())
                .select_from(AgentConversation)
                .where(
                    AgentConversation.owner_id == self.access.actor.id,
                    AgentConversation.archived.is_(False),
                )
            )
            require(count < 100, 429, "agent_capacity", "未归档会话已达100个，请先归档不用的会话")
        row.archived = archived
        row.version += 1
        row.updated_at = datetime.now(UTC)
        self.access.audit("agent.conversation.archived", row.id, {"archived": archived})
        return conversation_view(row)

    async def rename(self, identifier, version, title):
        row = await self.owned(identifier, lock=True)
        require(row.version == version, 409, "constraint_conflict", "会话版本已变化，请刷新")
        row.title = title.strip()
        row.version += 1
        row.updated_at = datetime.now(UTC)
        # 审计不复制可能含敏感信息的标题正文。
        self.access.audit("agent.conversation.renamed", row.id, {})
        return conversation_view(row)
