"""现有确认数据适配器；项目、权限和确认规则不进入通用查询内核。"""

import json
from datetime import UTC, datetime

from sqlalchemy import and_, func, select

from app.common.errors import require
from app.common.pagination import PAGE_BYTES
from app.infrastructure.models import Record, RecordConfirmation
from app.modules.query.compiler import compile_query, result_rows
from app.modules.query.schemas import AggregateQuery


class DataQuery:
    def __init__(self, data):
        self.data, self.access, self.db = data, data.access, data.db

    async def aggregate(self, identifier: str, revision: int, query: dict, scope="own"):
        schema = await self.data.schema(identifier, revision)
        self.access.need("record.read." + scope)
        conditions = [Record.project_id == identifier, ~Record.archived]
        if scope == "own":
            conditions.append(Record.owner_id == self.access.actor.id)
        latest = (
            select(
                RecordConfirmation.record_id,
                func.max(RecordConfirmation.revision).label("revision"),
            )
            .join(Record, Record.id == RecordConfirmation.record_id)
            .where(*conditions)
            .group_by(RecordConfirmation.record_id)
            .subquery()
        )
        # 先选每条记录最新确认，再筛结构版本；不可退回更老确认凑数。
        source = (
            select(RecordConfirmation.values, RecordConfirmation.schema_revision)
            .join(
                latest,
                and_(
                    RecordConfirmation.record_id == latest.c.record_id,
                    RecordConfirmation.revision == latest.c.revision,
                ),
            )
            .cte("authorized_source")
        )
        spec = AggregateQuery.model_validate(query)
        statement, columns = compile_query(source, schema["fields"], revision, spec)
        started = datetime.now(UTC)
        # 总请求超时、连接池、statement_timeout复用既有统一配置；取消由AsyncSession回滚。
        rows = (await self.db.execute(statement)).mappings().all()
        items = result_rows(rows, columns, spec.max_groups)
        require(
            len(json.dumps(items, ensure_ascii=False).encode()) <= PAGE_BYTES,
            422,
            "query_budget_exceeded",
            "聚合内容超过预算，未返回截断数据",
        )
        coverage = rows[0]
        return {
            "dataset_id": identifier,
            "schema_revision": revision,
            "scope": scope,
            "source_policy": "latest-confirmed/non-archived/exact-schema",
            "eligible_records": coverage["__eligible"],
            "excluded_schema_records": coverage["__eligible"] - coverage["__schema"],
            "matched_records": coverage["__matched"],
            "complete": True,
            "columns": columns,
            "rows": items,
            "read_started_at": started,
            "read_finished_at": datetime.now(UTC),
        }
