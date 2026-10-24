"""确认后的值只追加快照；更正候选不得替换尚有效的确认版本。"""

from sqlalchemy import select

from app.common.errors import require
from app.infrastructure.models import ProjectSchema, RecordConfirmation
from app.infrastructure.pagination import stream_page
from app.modules.data.schemas import ConfirmInput, CorrectionInput, validate_values
from app.modules.data.service import record_view


def confirmation_view(snapshot):
    return {
        key: getattr(snapshot, key)
        for key in (
            "record_id",
            "revision",
            "schema_revision",
            "values",
            "confirmed_by",
            "confirmed_at",
            "reason",
        )
    }


class Lifecycle:
    def __init__(self, access, data):
        self.access, self.data, self.db = access, data, access.db

    async def confirm(self, identifier: str, payload: dict):
        self.access.need("record.confirm")
        expected = ConfirmInput.model_validate(payload)
        record = await self.data.record(identifier, lock=True)
        require(record.version == expected.version, 409, "version_conflict", "记录已变更，请刷新")
        require(record.status == "submitted", 409, "record_frozen", "该工作版本已确认")
        project = await self.data.project(record.project_id, shared=True)
        require(not project.archived, 409, "project_archived", "项目已归档")
        snapshot = RecordConfirmation(
            record_id=record.id,
            revision=record.current_revision,
            schema_revision=record.schema_revision,
            values=record.values,
            confirmed_by=self.access.actor.id,
            reason=record.correction_reason,
        )
        self.db.add(snapshot)
        record.status = "confirmed"
        record.version += 1
        await self.db.flush()
        self.access.audit("record.confirmed", record.id, {"revision": snapshot.revision})
        return confirmation_view(snapshot)

    async def correct(self, identifier: str, payload: dict):
        expected = CorrectionInput.model_validate(payload)
        record = await self.data.record(identifier, mutate=True)
        require(record.version == expected.version, 409, "version_conflict", "记录已变更，请刷新")
        require(record.status == "confirmed", 409, "correction_pending", "当前工作版本尚未确认")
        require(record.current_revision < 2147483647, 409, "revision_exhausted", "修订号已达上限")
        project = await self.data.project(record.project_id, shared=True)
        require(not project.archived, 409, "project_archived", "项目已归档")
        schema = await self.db.get(ProjectSchema, (record.project_id, record.schema_revision))
        record.values = validate_values(schema.fields, expected.values)
        record.current_revision += 1
        record.correction_reason = expected.reason
        record.status = "submitted"
        record.version += 1
        self.access.audit(
            "record.correction.submitted", record.id, {"revision": record.current_revision}
        )
        return record_view(record)

    async def confirmed(self, identifier: str):
        await self.data.record(identifier)
        snapshot = await self.db.scalar(
            select(RecordConfirmation)
            .where(RecordConfirmation.record_id == identifier)
            .order_by(RecordConfirmation.revision.desc())
            .limit(1)
        )
        require(snapshot is not None, 404, "not_confirmed", "记录尚无确认版本")
        return confirmation_view(snapshot)

    async def history(self, identifier: str, after_revision: int = 0, limit: int = 50):
        await self.data.record(identifier)
        query = (
            select(RecordConfirmation)
            .where(
                RecordConfirmation.record_id == identifier,
                RecordConfirmation.revision > after_revision,
            )
            .order_by(RecordConfirmation.revision)
        )
        return await stream_page(self.db, query, confirmation_view, lambda row: row.revision, limit)
