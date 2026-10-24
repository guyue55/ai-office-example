"""真实PG/HTTP/SDK：大于常见小样例的确认导出可用，超限返回业务错误。"""

from uuid import uuid4

import pytest
from office_client import ServiceError
from test_lifecycle_live import confirmer as confirmer  # noqa: PLC0414


def test_large_confirmed_export_is_transportable_and_overflow_is_explicit(live, confirmer):
    admin = confirmer
    project = admin.request(
        "POST", "/api/v2/projects", json={"name": "导出预算验收-" + uuid4().hex}
    )
    fields = [
        {"id": f"f{i}", "label": f"字段{i}", "type": "text", "description": "仅构造预算测试"}
        for i in range(100)
    ]
    admin.request(
        "POST",
        f"/api/v2/projects/{project['id']}/schema",
        json={"expected_revision": 0, "fields": fields},
    )
    values = {field["id"]: "x" * 4000 for field in fields}
    for index in range(4):
        row = live["employee"].submit(
            project_id=project["id"],
            schema_revision=1,
            values=values,
            idempotency_key=uuid4().hex,
        )
        admin.confirm(row["id"], row["version"])
        if index == 2:
            exported = admin.export_confirmed(project["id"])
            assert len(exported["items"]) == 3
            assert all(item["values"] == values for item in exported["items"])
    with pytest.raises(ServiceError) as caught:
        admin.export_confirmed(project["id"])
    assert caught.value.status == 422
    assert caught.value.code == 422015
    assert "预算" in str(caught.value)
    assert not caught.value.outcome_unknown
