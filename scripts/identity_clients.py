"""既有OIDC客户端合同：企业安装与本机辅助器共享。"""


def ensure_client(http, realm_path, scope_id, client_id, redirect, audience):
    """幂等维护本项目开发客户端；不触及用户密码和业务授权。"""
    clients = http.get(realm_path + "/clients", params={"clientId": client_id})
    clients.raise_for_status()
    found = clients.json()
    current = found[0] if found else {}
    desired = {
        **current,
        "clientId": client_id,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": True,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": [redirect],
        "attributes": {**current.get("attributes", {}), "pkce.code.challenge.method": "S256"},
    }
    if not found:
        http.post(realm_path + "/clients", json=desired).raise_for_status()
        response = http.get(realm_path + "/clients", params={"clientId": client_id})
        response.raise_for_status()
        current = response.json()[0]
    else:
        http.put(realm_path + "/clients/" + current["id"], json=desired).raise_for_status()
    path = realm_path + "/clients/" + current["id"]
    response = http.get(path + "/protocol-mappers/models")
    response.raise_for_status()
    existing = next((row for row in response.json() if row["name"] == "office-audience"), None)
    mapper = {
        "name": "office-audience",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {
            "included.custom.audience": audience,
            "access.token.claim": "true",
            "id.token.claim": "false",
        },
    }
    if existing:
        http.put(
            path + "/protocol-mappers/models/" + existing["id"],
            json={**mapper, "id": existing["id"]},
        ).raise_for_status()
    else:
        http.post(path + "/protocol-mappers/models", json=mapper).raise_for_status()
    http.put(path + "/default-client-scopes/" + scope_id).raise_for_status()
