"""同员工三设备同时可用；定点停用一台不误伤另外两台。"""

import httpx
from conftest import browser_login


def test_same_employee_three_devices_and_targeted_disable(live):
    clients = [browser_login("employee") for _ in range(3)]
    rows = [
        client.describe_device(f"多设备验收-{index}", "独立浏览器")
        for index, client in enumerate(clients)
    ]
    assert len({row["id"] for row in rows}) == 3
    assert len({client.me()["id"] for client in clients}) == 1
    tokens = [client.store.load()["access_token"] for client in clients]
    live["operator"].change_authorization(
        rows[1]["id"], rows[1]["version"], "disable", "仅停用中间设备的隔离验证"
    )
    with httpx.Client(base_url=live["settings"].public_url) as http:
        statuses = [
            http.get("/api/v2/me", headers={"Authorization": "Bearer " + token}).status_code
            for token in tokens
        ]
    assert statuses == [200, 401, 200]
