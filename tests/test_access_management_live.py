"""授权管理体验对应的真实应用/API验证，数据仅写入独立开发环境。"""

from uuid import uuid4

import pytest
from office_client import ServiceError


def test_access_relations_counts_and_reverse_pagination(live):
    client = live["operator"]
    name = "授权链验证-" + uuid4().hex[:12]
    bundle = client.request(
        "POST", "/api/v2/access/permission-sets", json={"name": "  " + name + "  "}
    )
    client.request(
        "PUT",
        f"/api/v2/access/permission-sets/{bundle['id']}/permissions",
        json={"version": 1, "permissions": ["project.read", "record.read.own"]},
    )
    roles = []
    for index in range(2):
        role = client.request("POST", "/api/v2/access/roles", json={"name": f"{name}-{index}"})
        client.request(
            "PUT",
            f"/api/v2/access/roles/{role['id']}/permission-sets",
            json={"version": 1, "ids": [bundle["id"]]},
        )
        roles.append(role)
    detail = client.request(
        "GET", f"/api/v2/access/permission-sets/{bundle['id']}", params={"limit": 1}
    )
    assert detail["name"] == name and len(detail["permissions"]) == 2
    assert detail["permissions"][0]["name"] == "查看项目与字段"
    assert detail["used_by"]["has_more"] and detail["used_by"]["next"]
    second = client.request(
        "GET",
        f"/api/v2/access/permission-sets/{bundle['id']}",
        params={"limit": 1, "after": detail["used_by"]["next"]},
    )
    assert {detail["used_by"]["items"][0]["id"], second["used_by"]["items"][0]["id"]} == {
        row["id"] for row in roles
    }
    assert not second["used_by"]["has_more"]
    listed = client.request("GET", "/api/v2/access/roles", params={"q": name})
    assert len(listed["items"]) == 2
    assert all(
        row["permission_set_count"] == 1
        and row["permission_count"] == 2
        and row["employee_count"] == 0
        for row in listed["items"]
    )
    listed = client.request("GET", "/api/v2/access/permission-sets", params={"q": name})
    assert listed["items"][0]["role_count"] == 2 and listed["items"][0]["permission_count"] == 2
    role_detail = client.request("GET", f"/api/v2/access/roles/{roles[0]['id']}")
    assert role_detail["links"][0]["name"] == name
    assert {row["code"] for row in role_detail["permissions"]} == {
        "project.read",
        "record.read.own",
    }


def test_search_is_literal_and_duplicate_configuration_is_rejected(live):
    client = live["operator"]
    name = "UI-Reuse-" + uuid4().hex[:12]
    client.request("POST", "/api/v2/access/roles", json={"name": name})
    with pytest.raises(ServiceError) as caught:
        client.request("POST", "/api/v2/access/roles", json={"name": " " + name.lower() + " "})
    assert caught.value.status == 409
    assert len(client.request("GET", "/api/v2/access/roles", params={"q": name})["items"]) == 1
    special = "UI-%_" + uuid4().hex[:8]
    client.request("POST", "/api/v2/access/roles", json={"name": special})
    assert all(
        "%_" in row["name"]
        for row in client.request("GET", "/api/v2/access/roles", params={"q": "%_"})["items"]
    )
    with pytest.raises(ServiceError) as caught:
        client.request("GET", "/api/v2/access/roles", params={"q": "x" * 101})
    assert caught.value.status == 422


def test_detail_and_management_actions_keep_permission_boundary(live):
    client, user = live["operator"], live["employee"]
    detail_path = f"/api/v2/access/employees/{live['employee_id']}"
    with pytest.raises(ServiceError) as caught:
        user.request("GET", detail_path)
    assert caught.value.status == 403
    detail = client.request("GET", detail_path)
    assert detail["active"] and detail["links"]
    assert {p["code"] for p in detail["permissions"]} == set(live["permissions"])
    grant_path = f"/api/v2/access/permission-sets/{live['bundle']['id']}/permissions"
    before = client.request("GET", grant_path)
    changed = client.request(
        "PUT",
        grant_path,
        json={"version": before["version"], "permissions": live["permissions"] + ["access.read"]},
    )
    try:
        assert user.request("GET", detail_path)["id"] == live["employee_id"]
        with pytest.raises(ServiceError) as caught:
            user.request("POST", "/api/v2/access/roles", json={"name": "不得创建"})
        assert caught.value.status == 403
    finally:
        client.request(
            "PUT",
            grant_path,
            json={"version": changed["version"], "permissions": live["permissions"]},
        )
