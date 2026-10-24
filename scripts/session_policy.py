"""已确认的身份超时策略；只更新列明字段，保留其他客户端属性。"""

import json
from pathlib import Path

from office_client.product import PRODUCT

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "deploy/identity/session-policy.json"
REALM = "/admin/realms/office"


def policy():
    return json.loads(POLICY.read_text())


def admin_headers(http):
    response = http.post(
        "http://127.0.0.1:8081/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "foundation-admin",
            "password": (ROOT / "runtime/secrets/admin_password").read_text().strip(),
        },
    )
    if response.status_code != 200:
        raise RuntimeError("隔离策略管理认证失败 status=" + str(response.status_code))
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def read_policy(http, headers):
    expected = policy()
    response = http.get("http://127.0.0.1:8081" + REALM, headers=headers)
    response.raise_for_status()
    result = {"realm": {key: response.json().get(key) for key in expected["realm"]}, "clients": {}}
    for name, attributes in expected["clients"].items():
        response = http.get(
            "http://127.0.0.1:8081" + REALM + "/clients", params={"clientId": name}, headers=headers
        )
        response.raise_for_status()
        clients = response.json()
        if len(clients) != 1:
            raise RuntimeError("策略要求已有且唯一的项目客户端")
        item = clients[0]
        result["clients"][name] = {key: item.get("attributes", {}).get(key) for key in attributes}
    return result


def _client_attributes(http, headers, name, attributes):
    response = http.get(
        "http://127.0.0.1:8081" + REALM + "/clients", params={"clientId": name}, headers=headers
    )
    response.raise_for_status()
    clients = response.json()
    if len(clients) != 1:
        raise RuntimeError("身份客户端不存在或不唯一")
    item = clients[0]
    values = dict(item.get("attributes", {}))
    for key, value in attributes.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    response = http.put(
        "http://127.0.0.1:8081" + REALM + "/clients/" + item["id"],
        headers=headers,
        json={**item, "attributes": values},
    )
    if response.status_code != 204:
        raise RuntimeError("身份客户端策略写入失败 status=" + str(response.status_code))


def apply_policy(http, headers, desired):
    # 先限制Web，再改变父会话，最后更新完整客户端策略；子期限不能先超出父期限。
    response = http.get("http://127.0.0.1:8081" + REALM, headers=headers)
    response.raise_for_status()
    current = response.json()
    web = desired["clients"][PRODUCT["web_client_id"]]
    staged = {}
    for attribute, realm_key in (
        ("client.session.idle.timeout", "ssoSessionIdleTimeout"),
        ("client.session.max.lifespan", "ssoSessionMaxLifespan"),
    ):
        final = int(web.get(attribute) or desired["realm"][realm_key])
        staged[attribute] = str(min(current[realm_key], desired["realm"][realm_key], final))
    _client_attributes(http, headers, PRODUCT["web_client_id"], staged)
    response = http.put("http://127.0.0.1:8081" + REALM, headers=headers, json=desired["realm"])
    if response.status_code != 204:
        raise RuntimeError("身份父会话策略写入失败 status=" + str(response.status_code))
    for name, attributes in desired["clients"].items():
        _client_attributes(http, headers, name, attributes)
