"""真实PG：项目创建并发幂等、确认预演及旧接口兼容。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from office_client import ServiceError


def test_project_creation_idempotency_and_legacy_compatibility(live):
    admin = live["operator"]
    payload = {"name": "项目幂等-" + uuid4().hex[:10], "idempotency_key": uuid4().hex}

    def create():
        return admin.request("POST", "/api/v2/projects", json=payload)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: create(), range(3)))
    assert len({r["id"] for r in results}) == 1
    with pytest.raises(ServiceError) as conflict:
        admin.request("POST", "/api/v2/projects", json={**payload, "name": "其他内容"})
    assert conflict.value.status == 409
    legacy = {"name": "旧接口兼容-" + uuid4().hex[:10]}
    first = admin.request("POST", "/api/v2/projects", json=legacy)
    second = admin.request("POST", "/api/v2/projects", json=legacy)
    assert first["id"] != second["id"]  # 同名不是重复请求，旧行为保持。
    audits = admin.access_list("audit", action="project.created", q=results[0]["id"])
    assert len(audits["items"]) == 1


def test_project_preview_preserves_key_and_replay_metadata(live):
    admin = live["operator"]
    preview = admin.preview_project("安全创建预览-" + uuid4().hex[:10])
    change, digest = preview["change"], preview["confirmation_digest"]
    assert change["payload"]["idempotency_key"]
    before = admin.projects()
    first = admin.apply_data_change(change, digest)
    again = admin.apply_data_change(change, digest)
    assert first["result"]["id"] == again["result"]["id"]
    assert first["result"]["id"] not in {row["id"] for row in before["items"]}
    # 预览只是保存点回滚，不能冒充已执行写入或给只读响应标记重放。
    assert admin.preview_data_change(change)["change"] == change
    headers = {"Authorization": "Bearer " + admin.store.load()["access_token"]}
    with httpx.Client(base_url=live["settings"].public_url, headers=headers) as http:
        proposed = http.post("/api/v2/data-changes/preview", json={"change": change})
        assert (
            proposed.status_code == 200 and proposed.json()["meta"].get("replayed", False) is False
        )
        replayed = http.post(
            "/api/v2/data-changes/apply", json={"change": change, "confirmation_digest": digest}
        )
        assert (
            replayed.status_code == 200 and replayed.json()["meta"].get("replayed", False) is True
        )


def test_project_keys_are_actor_scoped_and_current_permission_is_required(live):
    admin, user = live["operator"], live["employee"]
    path = f"/api/v2/access/permission-sets/{live['bundle']['id']}/permissions"
    original = admin.request("GET", path)
    payload = {"name": "创建归属范围-" + uuid4().hex[:8], "idempotency_key": uuid4().hex}
    first = admin.request("POST", "/api/v2/projects", json=payload)
    try:
        admin.request(
            "PUT",
            path,
            json={
                "version": original["version"],
                "permissions": [*original["ids"], "project.manage"],
            },
        )
        second = user.request("POST", "/api/v2/projects", json=payload)
        assert first["id"] != second["id"]
        current = admin.request("GET", path)
        admin.request(
            "PUT", path, json={"version": current["version"], "permissions": original["ids"]}
        )
        with pytest.raises(ServiceError) as denied:
            user.request("POST", "/api/v2/projects", json=payload)
        assert denied.value.status == 403
    finally:
        current = admin.request("GET", path)
        admin.request(
            "PUT", path, json={"version": current["version"], "permissions": original["ids"]}
        )
