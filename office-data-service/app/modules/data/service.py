import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import and_, func, select

from app.common.errors import require
from app.common.pagination import PAGE_BYTES
from app.infrastructure.models import (
    Project,
    ProjectCreation,
    ProjectSchema,
    Record,
    RecordConfirmation,
)
from app.infrastructure.pagination import stream_page
from app.modules.data.schemas import (
    ProjectInput,
    ProjectUpdate,
    RecordInput,
    RecordUpdate,
    SchemaInput,
    validate_values,
)

EXPORT_ITEM_NODES = 99_000  # 低于契约100000节点，为响应外壳与元数据预留空间。


def project_view(obj):
    return {
        key: getattr(obj, key) for key in ("id", "name", "version", "archived", "schema_revision")
    }


def record_view(obj):
    return {
        key: getattr(obj, key)
        for key in (
            "id",
            "project_id",
            "schema_revision",
            "owner_id",
            "values",
            "version",
            "archived",
            "status",
        )
    }


class Data:
    def __init__(self, access):
        self.access, self.db = access, access.db

    async def project(self, identifier: str, lock=False, shared=False):
        obj = await self.db.get(
            Project, identifier, with_for_update={"read": True} if shared else lock
        )
        require(obj is not None, 404, "not_found", "项目不存在")
        return obj

    async def projects(self, after: str = "", limit: int = 50):
        self.access.need("project.read")
        require(1 <= limit <= 100, 422, "invalid_limit", "每页数量必须为1–100")
        rows = (
            await self.db.scalars(
                select(Project).where(Project.id > after).order_by(Project.id).limit(limit + 1)
            )
        ).all()
        return {
            "items": [project_view(p) for p in rows[:limit]],
            "next": rows[limit - 1].id if len(rows) > limit else None,
        }

    async def export_confirmed(self, identifier: str, max_records: int = 1000):
        self.access.need("project.read")
        await self.project(identifier)
        conditions = [Record.project_id == identifier, ~Record.archived]
        scope = "all" if self.access.allows("record.read.all") else "own"
        if scope == "own":
            self.access.need("record.read.own")
            conditions.append(Record.owner_id == self.access.actor.id)
        started = datetime.now(UTC)
        # PostgreSQL单条查询的一致性快照：只选每条记录最新确认值，候选更正不影响正式值。
        latest = (
            select(
                RecordConfirmation.record_id,
                func.max(RecordConfirmation.revision).label("revision"),
            )
            .join(Record, Record.id == RecordConfirmation.record_id)
            .where(*conditions)
            .group_by(RecordConfirmation.record_id)
            .order_by(RecordConfirmation.record_id)
            .limit(max_records + 1)
            .subquery()
        )
        query = (
            select(RecordConfirmation)
            .join(
                latest,
                and_(
                    RecordConfirmation.record_id == latest.c.record_id,
                    RecordConfirmation.revision == latest.c.revision,
                ),
            )
            .order_by(RecordConfirmation.record_id)
        )
        fields = (
            "record_id",
            "revision",
            "schema_revision",
            "values",
            "confirmed_by",
            "confirmed_at",
            "reason",
        )
        items: list[dict] = []
        total_bytes = 0
        total_nodes = 0
        # 动态字段可能很大：同时约束行数和内容字节；服务端游标小批读取，禁止先all()。
        stream = await self.db.stream_scalars(query.execution_options(yield_per=10))
        try:
            async for row in stream:
                require(
                    len(items) < max_records,
                    422,
                    "confirmed_export_budget_exceeded",
                    "确认数据超过本次导出条数上限；未返回截断数据",
                )
                item = {key: getattr(row, key) for key in fields}
                total_bytes += len(json.dumps(item, ensure_ascii=False, default=str).encode())
                # 确认值由字段契约限定为扁平标量；为外壳、元数据和列表预留1000节点。
                total_nodes += 1 + len(item) + len(item["values"])
                require(
                    total_bytes <= PAGE_BYTES and total_nodes <= EXPORT_ITEM_NODES,
                    422,
                    "confirmed_export_budget_exceeded",
                    "确认数据超过本次内容或结构预算；需缩小数据范围，未返回截断数据",
                )
                items.append(item)
        finally:
            await stream.close()
        return {
            "scope": scope,
            "project_id": identifier,
            "read_started_at": started,
            "read_finished_at": datetime.now(UTC),
            "items": items,
        }

    async def create_project(self, payload: dict):
        self.access.need("project.manage")
        data = ProjectInput.model_validate(payload)
        digest = hashlib.sha256(
            json.dumps({"name": data.name}, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if data.idempotency_key is not None:
            await self.db.refresh(self.access.actor, with_for_update=True)
            existing = await self.db.get(
                ProjectCreation, (self.access.actor.id, data.idempotency_key)
            )
            if existing is not None:
                require(
                    existing.request_digest == digest,
                    409,
                    "idempotency_conflict",
                    "幂等键已用于其他项目创建内容",
                )
                self.db.info["idempotency_replayed"] = True
                return project_view(await self.project(existing.project_id))
        obj = Project(name=data.name)
        self.db.add(obj)
        (await self.db.flush())
        if data.idempotency_key is not None:
            self.db.add(
                ProjectCreation(
                    actor_id=self.access.actor.id,
                    idempotency_key=data.idempotency_key,
                    project_id=obj.id,
                    request_digest=digest,
                )
            )
        self.access.audit("project.created", obj.id)
        return project_view(obj)

    async def update_project(self, identifier: str, payload: dict):
        self.access.need("project.manage")
        data = ProjectUpdate.model_validate(payload)
        obj = await self.project(identifier, lock=True)
        require(obj.version == data.version, 409, "version_conflict", "项目已修改")
        if data.name is not None:
            obj.name = data.name
        if data.archived is not None:
            obj.archived = data.archived
        obj.version += 1
        self.access.audit("project.updated", obj.id)
        return project_view(obj)

    async def schema(self, identifier: str, revision: int | None = None):
        self.access.need("project.read")
        project = await self.project(identifier)
        revision = project.schema_revision if revision is None else revision
        obj = await self.db.get(ProjectSchema, (identifier, revision))
        require(obj is not None, 404, "schema_not_found", "字段定义尚未发布")
        return {"project_id": identifier, "revision": obj.revision, "fields": obj.fields}

    async def publish_schema(self, identifier: str, payload: dict):
        self.access.need("project.manage")
        data = SchemaInput.model_validate(payload)
        obj = await self.project(identifier, lock=True)
        require(not obj.archived, 409, "project_archived", "项目已归档")
        require(
            obj.schema_revision == data.expected_revision, 409, "schema_conflict", "字段定义已变更"
        )
        obj.schema_revision += 1
        obj.version += 1
        fields = [field.model_dump() for field in data.fields]
        self.db.add(ProjectSchema(project_id=obj.id, revision=obj.schema_revision, fields=fields))
        self.access.audit("schema.published", obj.id, {"revision": obj.schema_revision})
        return {"project_id": identifier, "revision": obj.schema_revision, "fields": fields}

    async def create_record(self, payload: dict):
        self.access.need("record.create")
        data = RecordInput.model_validate(payload)
        digest = hashlib.sha256(
            json.dumps(
                data.model_dump(exclude={"idempotency_key"}), sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        # 授权共享锁之后锁员工行，将同一员工的幂等键提交串行化。
        (await self.db.refresh(self.access.actor, with_for_update=True))
        existing = await self.db.scalar(
            select(Record).where(
                Record.owner_id == self.access.actor.id,
                Record.idempotency_key == data.idempotency_key,
            )
        )
        if existing:
            require(
                existing.request_digest == digest,
                409,
                "idempotency_conflict",
                "幂等键已用于其他内容",
            )
            # 重放返回当前实体，读取权可能已撤销，不能用旧键绕过当前授权。
            if not self.access.allows("record.read.all"):
                self.access.need("record.read.own")
            self.db.info["idempotency_replayed"] = True
            return record_view(existing)
        obj = await self.project(data.project_id, shared=True)
        require(not obj.archived, 409, "project_archived", "项目已归档")
        require(
            obj.schema_revision == data.schema_revision,
            409,
            "schema_conflict",
            "请刷新项目字段定义",
        )
        definition = await self.db.get(ProjectSchema, (obj.id, data.schema_revision))
        require(definition is not None, 409, "schema_not_found", "项目字段定义未发布")
        values = validate_values(definition.fields, data.values)
        record = Record(
            project_id=obj.id,
            schema_revision=data.schema_revision,
            owner_id=self.access.actor.id,
            values=values,
            idempotency_key=data.idempotency_key,
            request_digest=digest,
        )
        self.db.add(record)
        (await self.db.flush())
        self.access.audit("record.created", record.id, {"project_id": obj.id})
        return record_view(record)

    async def records(self, project_id: str | None = None, after: str = "", limit: int = 50):
        require(1 <= limit <= 100, 422, "invalid_limit", "每页数量必须为1–100")
        query = select(Record).where(Record.id > after, ~Record.archived)
        if not self.access.allows("record.read.all"):
            self.access.need("record.read.own")
            query = query.where(Record.owner_id == self.access.actor.id)
        if project_id:
            query = query.where(Record.project_id == project_id)
        return await stream_page(
            self.db, query.order_by(Record.id), record_view, lambda row: row.id, limit
        )

    async def record(self, identifier: str, mutate=False, lock=False):
        prefix = "record.manage" if mutate else "record.read"
        all_records = self.access.allows(prefix + ".all")
        if not all_records:
            self.access.need(prefix + ".own")
        query = select(Record).where(Record.id == identifier, ~Record.archived)
        if not all_records:
            query = query.where(Record.owner_id == self.access.actor.id)
        if mutate or lock:
            query = query.with_for_update()
        obj = await self.db.scalar(query)
        require(obj is not None, 404, "not_found", "记录不存在")
        return obj

    async def get_record(self, identifier: str):
        return record_view(await self.record(identifier))

    async def update_record(self, identifier: str, payload: dict):
        data = RecordUpdate.model_validate(payload)
        obj = await self.record(identifier, mutate=True)
        require(obj.version == data.version, 409, "version_conflict", "记录已变更，请刷新")
        require(
            obj.status == "submitted", 409, "record_frozen", "已确认版本禁止直接修改，请提交更正"
        )
        require(
            not (await self.project(obj.project_id, shared=True)).archived,
            409,
            "project_archived",
            "项目已归档",
        )
        schema = await self.db.get(ProjectSchema, (obj.project_id, obj.schema_revision))
        obj.values = validate_values(schema.fields, data.values)
        obj.version += 1
        self.access.audit("record.updated", obj.id)
        return record_view(obj)

    async def archive_record(self, identifier: str, version: int):
        obj = await self.record(identifier, mutate=True)
        require(obj.version == version, 409, "version_conflict", "记录已变更")
        published = await self.db.scalar(
            select(RecordConfirmation.revision)
            .where(RecordConfirmation.record_id == obj.id)
            .limit(1)
        )
        require(published is None, 409, "record_frozen", "已有确认版本的记录禁止归档")
        obj.archived = True
        obj.version += 1
        self.access.audit("record.archived", obj.id)
        return {"id": obj.id, "version": obj.version, "archived": True}
