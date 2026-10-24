"""专属新实例的身份引导；使用已有OIDC客户端合同与已确认期限策略。"""

import json
import sys
from pathlib import Path

from product import PRODUCT

sys.path.insert(0, "/helpers")
from identity_clients import ensure_client


def configure(http, root, config):
    base = "http://identity:8080/identity"
    response = http.post(
        base + "/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "setup-admin",
            "password": (root / "identity/secrets/admin_password").read_text().strip(),
        },
    )
    response.raise_for_status()
    http.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    realm = base + "/admin/realms/office"

    def request(method, url="", **kwargs):
        result = http.request(method, realm + url, **kwargs)
        result.raise_for_status()
        return result.json() if result.content else None

    if http.get(realm).status_code == 404:
        response = http.post(
            base + "/admin/realms",
            json={"realm": "office", "enabled": True, "registrationAllowed": False},
        )
        response.raise_for_status()
    desired = json.loads(Path("/policy.json").read_text())
    request(
        "PUT",
        json={
            **desired["realm"],
            "displayName": PRODUCT["name"] + "（HTTP验收）"
            if config.get("allow_lan_http_demo")
            else PRODUCT["name"],
            "sslRequired": "none" if config.get("allow_lan_http_demo") else "external",
            "loginTheme": "office",
            "internationalizationEnabled": True,
            "supportedLocales": ["zh-CN", "en"],
            "defaultLocale": "zh-CN",
        },
    )
    scopes = request("GET", "/client-scopes")
    scope = next((row for row in scopes if row["name"] == "office"), None)
    if scope is None:
        request(
            "POST",
            "/client-scopes",
            json={
                "name": "office",
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "true"},
            },
        )
        scope = next(row for row in request("GET", "/client-scopes") if row["name"] == "office")
    for client_id, redirect in [
        (PRODUCT["client_id"], "http://127.0.0.1:8765/callback"),
        (PRODUCT["web_client_id"], config["public_url"] + "/auth/callback"),
    ]:
        ensure_client(http, realm, scope["id"], client_id, redirect, config["public_url"] + "/mcp")
        client = request("GET", "/clients", params={"clientId": client_id})[0]
        request(
            "PUT",
            "/clients/" + client["id"],
            json={
                **client,
                "attributes": {**client.get("attributes", {}), **desired["clients"][client_id]},
            },
        )
    values = {}
    for name, service_account in [
        (PRODUCT["admin_client_id"], True),
        (config["public_url"] + "/mcp", False),
    ]:
        found = request("GET", "/clients", params={"clientId": name})
        if not found:
            request(
                "POST",
                "/clients",
                json={
                    "clientId": name,
                    "protocol": "openid-connect",
                    "enabled": True,
                    "publicClient": False,
                    "standardFlowEnabled": False,
                    "directAccessGrantsEnabled": False,
                    "serviceAccountsEnabled": service_account,
                    "fullScopeAllowed": False,
                },
            )
            found = request("GET", "/clients", params={"clientId": name})
        client = found[0]
        if service_account:
            account = request("GET", "/clients/" + client["id"] + "/service-account-user")
            manager = request("GET", "/clients", params={"clientId": "realm-management"})[0]
            roles = [
                request("GET", f"/clients/{manager['id']}/roles/{role}")
                for role in ("query-users", "view-users", "manage-users")
            ]
            request(
                "POST", f"/users/{account['id']}/role-mappings/clients/{manager['id']}", json=roles
            )
            request(
                "POST",
                f"/clients/{client['id']}/scope-mappings/clients/{manager['id']}",
                json=roles,
            )
        values["identity_admin_secret" if service_account else "introspection_secret"] = request(
            "GET", f"/clients/{client['id']}/client-secret"
        )["value"]
    profile = request("GET", "/users/profile")
    for field in profile["attributes"]:
        if field["name"] in ("email", "firstName", "lastName"):
            field.pop("required", None)
    if not any(field["name"] == "officeOperation" for field in profile["attributes"]):
        profile["attributes"].append(
            {
                "name": "officeOperation",
                "displayName": "内部开户标记",
                "permissions": {"view": ["admin"], "edit": ["admin"]},
                "validations": {"length": {"max": 100}},
            }
        )
    request("PUT", "/users/profile", json=profile)
    users = request("GET", "/users", params={"username": config["admin_username"], "exact": "true"})
    if not users:
        request(
            "POST",
            "/users",
            json={
                "username": config["admin_username"],
                "enabled": True,
                "firstName": config["admin_name"],
                "credentials": [
                    {
                        "type": "password",
                        "value": (root / "credentials/initial_password").read_text().strip(),
                        "temporary": True,
                    }
                ],
            },
        )
        users = request(
            "GET", "/users", params={"username": config["admin_username"], "exact": "true"}
        )
    return {"subject": users[0]["id"], "name": config["admin_name"]}, values
