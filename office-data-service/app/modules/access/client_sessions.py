"""登录会话登记与即时业务撤销；所有入口共用，原始令牌永不持久化。"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Text, cast, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.common.errors import require
from app.infrastructure.models import ClientAuthorization, Employee
from app.infrastructure.pagination import numbered_rows
from app.modules.access.session_errors import SessionEnded


async def check_session(access, principal):
    claims = principal.claims
    sid, client = claims.get("sid"), claims.get("azp")
    # 仅内部任务可无sid；外部令牌校验器强制校验sid/azp，不能由调用参数决定。
    if not sid:
        return None
    require(isinstance(sid, str) and isinstance(client, str), 401, "invalid_token", "登录会话无效")
    now = datetime.now(UTC)
    statement = (
        insert(ClientAuthorization)
        .values(
            id=str(uuid4()),
            issuer=principal.issuer,
            session_id=sid,
            employee_id=access.actor.id,
            clients=[client],
            device_name="",
            platform="",
            state="active",
            version=1,
            authenticated_at=datetime.fromtimestamp(claims.get("auth_time", claims["iat"]), UTC),
            created_at=now,
            last_seen_at=now,
            token_expires_at=datetime.fromtimestamp(claims["exp"], UTC),
            cleanup="not_requested",
        )
        .on_conflict_do_nothing(constraint="uq_client_authorization_session")
        .returning(ClientAuthorization.id)
    )
    lookup = select(ClientAuthorization).where(
        ClientAuthorization.issuer == principal.issuer, ClientAuthorization.session_id == sid
    )
    row = await access.db.scalar(lookup)
    created = None
    if row is None:
        # 已登记会话只读当前状态；并发首次登记才走原子插入，避免每次请求写冲突。
        created = await access.db.scalar(statement)
        row = await access.db.scalar(lookup)
    if (
        row is None
        or row.employee_id != access.actor.id
        or row.state != "active"
        or row.archived_at is not None
    ):
        raise SessionEnded()
    if created:
        access.audit(
            "session.created", row.id, {"employee_name": access.actor.name, "clients": [client]}
        )
    elif now - row.last_seen_at >= timedelta(seconds=60) or client not in row.clients:
        # 在会话行上串行更新，避免并发不同客户端互相覆盖已观察到的azp。
        await access.db.refresh(row, with_for_update=True)
        row.clients = sorted(set(row.clients) | {client})
        row.last_seen_at = now
        row.token_expires_at = datetime.fromtimestamp(claims["exp"], UTC)
    return row


class ClientSessions:
    def __init__(self, access):
        self.access, self.db = access, access.db

    async def view(self, row, employee_name=None):
        if employee_name is None:
            employee_name = await self.db.scalar(
                select(Employee.name).where(Employee.id == row.employee_id)
            )
        return {
            "id": row.id,
            "version": row.version,
            "employee_id": row.employee_id,
            "employee_name": employee_name,
            "clients": row.clients,
            "device_name": row.device_name,
            "platform": row.platform,
            "state": row.state,
            "current": self.access.client_session is not None
            and self.access.client_session.id == row.id,
            **{
                field: getattr(row, field)
                for field in (
                    "authenticated_at",
                    "created_at",
                    "last_seen_at",
                    "token_expires_at",
                    "ended_at",
                    "archived_at",
                    "cleanup",
                )
            },
        }

    async def listing(self, **filters):
        scope = filters.pop("scope", "own")
        if scope == "all":
            self.access.need("session.read.all")
        conditions = (
            [] if scope == "all" else [ClientAuthorization.employee_id == self.access.actor.id]
        )
        after, limit = filters.pop("after", ""), filters.pop("limit", 20)
        page = filters.pop("page", None)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"scope": scope, "actor": self.access.actor.id, **filters}, sort_keys=True
            ).encode()
        ).hexdigest()[:16]
        conditions.append(
            ClientAuthorization.archived_at.is_not(None)
            if filters.get("archived")
            else ClientAuthorization.archived_at.is_(None)
        )
        if filters.get("employee_id"):
            conditions.append(ClientAuthorization.employee_id == filters["employee_id"])
        if filters.get("state"):
            conditions.append(ClientAuthorization.state == filters["state"])
        if filters.get("client_id"):
            conditions.append(ClientAuthorization.clients.contains([filters["client_id"]]))
        if filters.get("q"):
            escaped = filters["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = "%" + escaped + "%"
            conditions.append(
                or_(
                    *[
                        column.ilike(pattern, escape="\\")
                        for column in (
                            ClientAuthorization.id,
                            ClientAuthorization.device_name,
                            ClientAuthorization.platform,
                            cast(ClientAuthorization.clients, Text),
                            Employee.name,
                        )
                    ]
                )
            )
        field = getattr(ClientAuthorization, filters.get("date_field", "created_at"))
        for key, comparison in (("since", field.__ge__), ("until", field.__lt__)):
            if filters.get(key):
                conditions.append(comparison(datetime.fromisoformat(filters[key])))
        base = select(ClientAuthorization, Employee.name).join(Employee).where(*conditions)
        if after:
            parts = after.split(":")
            require(
                len(parts) == 2 and parts[1] == fingerprint,
                422,
                "invalid_query",
                "分页条件已改变，请返回首页",
            )
            anchor = await self.db.scalar(base.where(ClientAuthorization.id == parts[0]))
            require(anchor is not None, 422, "invalid_query", "分页位置已失效，请返回首页")
            base = base.where(
                tuple_(ClientAuthorization.created_at, ClientAuthorization.id)
                < (anchor.created_at, anchor.id)
            )
        ordered = base.order_by(
            ClientAuthorization.created_at.desc(), ClientAuthorization.id.desc()
        )
        pagination = None
        if page is not None:
            rows, pagination = await numbered_rows(self.db, ordered, page, limit, scalars=False)
        else:
            rows = (await self.db.execute(ordered.limit(limit + 1))).all()
        return {
            "items": [await self.view(row, name) for row, name in rows[:limit]],
            "pagination": pagination,
            "has_more": pagination["page"] < pagination["pages"]
            if pagination
            else len(rows) > limit,
            "next": rows[limit - 1][0].id + ":" + fingerprint if len(rows) > limit else None,
        }

    async def register(self, device_name=None, platform=None):
        row = self.access.client_session
        require(row is not None, 401, "invalid_token", "需要浏览器登录授权")
        device_name = row.device_name if device_name is None else device_name
        platform = row.platform if platform is None else platform
        if (row.device_name, row.platform) != (device_name, platform):
            row.device_name, row.platform = device_name, platform
            row.version += 1
            self.access.audit(
                "session.device",
                row.id,
                {
                    "employee_name": self.access.actor.name,
                    "device_name": device_name,
                    "platform": platform,
                },
            )
        return await self.view(row)

    async def change(self, identifier, version, action, reason):
        row = await self.db.get(ClientAuthorization, identifier)
        require(row is not None, 404, "not_found", "授权记录不存在")
        if row.employee_id != self.access.actor.id:
            self.access.need("session.manage.all")
        require(row.version == version, 409, "version_conflict", "授权已变更，请刷新后重试")
        if action == "retry":
            require(
                row.state != "active" and row.cleanup in ("failed", "pending"),
                409,
                "constraint_conflict",
                "无需重试清理",
            )
        else:
            require(row.archived_at is None, 409, "constraint_conflict", "授权已归档")
            row.state = "disabled" if action == "disable" else "revoked"
            row.ended_at = row.ended_at or datetime.now(UTC)
            if action == "archive":
                row.archived_at = datetime.now(UTC)
        row.version += 1
        row.cleanup = "pending"
        self.access.revision.revision += 1
        name = await self.db.scalar(select(Employee.name).where(Employee.id == row.employee_id))
        self.access.audit(
            "session." + action,
            row.id,
            {
                "employee_name": name,
                "device_name": row.device_name,
                "clients": row.clients,
                "reason": reason,
            },
        )
        return row
