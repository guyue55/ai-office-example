"""真实PG/HTTP分页：真实总数、范围、跳页、过滤和权限隔离。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.infrastructure.database import sessions
from app.infrastructure.models import Audit
from office_client import ServiceError
from sqlalchemy import select


def test_numbered_access_lists_and_cursor_compatibility(live):
    operator = live["operator"]
    prefix = "分页验收-" + uuid4().hex[:8]
    ids = {
        operator.request("POST", "/api/v2/access/roles", json={"name": prefix + str(index)})["id"]
        for index in range(23)
    }
    first = operator.access_list("roles", page=1, limit=10, q=prefix)
    assert first["pagination"] == {
        "page": 1,
        "page_size": 10,
        "total": 23,
        "pages": 3,
        "start": 1,
        "end": 10,
    }
    second = operator.access_list("roles", page=2, limit=10, q=prefix)
    last = operator.access_list("roles", page=999, limit=10, q=prefix)
    assert last["pagination"]["page"] == 3 and last["pagination"]["end"] == 23
    assert {row["id"] for page in (first, second, last) for row in page["items"]} == ids
    empty = operator.access_list("roles", page=999, q=prefix + "不存在")
    assert empty["pagination"]["total"] == 0 and empty["pagination"]["start"] == 0
    cursor = operator.access_list("roles", limit=10, q=prefix)
    assert cursor["pagination"] is None and cursor["next"]
    follow = operator.access_list("roles", limit=10, q=prefix, after=cursor["next"])
    assert not {r["id"] for r in cursor["items"]} & {r["id"] for r in follow["items"]}
    with pytest.raises(ServiceError) as invalid:
        operator.access_list("roles", page=2, after=cursor["next"])
    assert invalid.value.status == 422
    for kind in ("employees", "roles", "permission-sets", "permissions", "audit"):
        page = operator.access_list(kind, page=1, limit=20)
        assert page["pagination"]["page_size"] == 20 and page["pagination"]["total"] >= len(
            page["items"]
        )


def test_audit_combined_filters_and_numbered_histories(live):
    operator = live["operator"]
    identifier = operator.request(
        "POST", "/api/v2/access/roles", json={"name": "审计组合条件-" + uuid4().hex[:8]}
    )["id"]
    with sessions(live["settings"])() as db:
        event = db.scalar(select(Audit).where(Audit.resource_id == identifier))
        moment = event.created_at
    result = operator.access_list(
        "audit",
        q=identifier,
        actor=operator.me()["name"],
        action="access.changed",
        since=(moment - timedelta(seconds=1)).isoformat(),
        until=(moment + timedelta(seconds=1)).isoformat(),
        page=1,
        limit=20,
    )
    assert result["pagination"]["total"] == 1 and result["items"][0]["resource_id"] == identifier
    assert not operator.access_list("audit", actor="不匹配操作者", page=1)["items"]
    history = operator.request("GET", "/api/v2/account-operations", params={"page": 1, "limit": 10})
    assert history["pagination"]["page_size"] == 10
    detail = operator.request(
        "GET", f"/api/v2/access/roles/{identifier}", params={"page": 1, "limit": 10}
    )
    assert detail["used_by"]["pagination"]["total"] == 0
    own = live["employee"].client_authorizations(page=1, limit=20)
    assert all(row["employee_id"] == live["employee_id"] for row in own["items"])
    assert own["pagination"]["total"] >= len(own["items"])
    with pytest.raises(ServiceError) as denied:
        live["employee"].client_authorizations(scope="all", page=1)
    assert denied.value.status == 403
