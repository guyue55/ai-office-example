"""显式初始化独立本机身份管理连接；不重置已有员工密码或业务权限。"""

import json
import os

import httpx
from dev_stack import ROOT, secret, wait_for
from office_client.product import PRODUCT


def configure():
    path = ROOT / "runtime/foundation/config.json"
    config = json.loads(path.read_text())
    if config["issuer"] != "http://127.0.0.1:8081/realms/office":
        raise RuntimeError("此辅助脚本仅允许独立本机开发realm")
    wait_for("http://127.0.0.1:8081/realms/master/.well-known/openid-configuration", seconds=30)
    realm = "/admin/realms/office"
    client_name = PRODUCT["admin_client_id"]
    with httpx.Client(base_url="http://127.0.0.1:8081", timeout=10, follow_redirects=False) as http:
        response = http.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "foundation-admin",
                "password": secret("admin_password"),
            },
        )
        response.raise_for_status()
        http.headers["Authorization"] = "Bearer " + response.json()["access_token"]

        def request(method, url, **kwargs):
            result = http.request(method, realm + url, **kwargs)
            result.raise_for_status()
            return result.json() if result.content else None

        found = request("GET", "/clients", params={"clientId": client_name})
        if not found:
            request(
                "POST",
                "/clients",
                json={
                    "clientId": client_name,
                    "enabled": True,
                    "publicClient": False,
                    "serviceAccountsEnabled": True,
                    "standardFlowEnabled": False,
                    "directAccessGrantsEnabled": False,
                    "fullScopeAllowed": False,
                    "protocol": "openid-connect",
                },
            )
            found = request("GET", "/clients", params={"clientId": client_name})
        client = found[0]
        if not client.get("serviceAccountsEnabled") or client.get("fullScopeAllowed"):
            raise RuntimeError("已有管理客户端配置与预期不同，请人工核对；未覆盖")
        identifier = client["id"]
        account = request("GET", f"/clients/{identifier}/service-account-user")
        manager = request("GET", "/clients", params={"clientId": "realm-management"})[0]
        roles = [
            request("GET", f"/clients/{manager['id']}/roles/{name}")
            for name in ("query-users", "view-users", "manage-users")
        ]
        request("POST", f"/users/{account['id']}/role-mappings/clients/{manager['id']}", json=roles)
        request("POST", f"/clients/{identifier}/scope-mappings/clients/{manager['id']}", json=roles)
        profile = request("GET", "/users/profile")
        # 中文姓名和无邮箱路径，避免首次登录要求业务人员补填无用字段。
        for field in profile["attributes"]:
            if field["name"] in ("lastName", "email"):
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
        request(
            "PUT",
            "",
            json={
                "displayName": PRODUCT["name"],
                "loginTheme": "office",
                "internationalizationEnabled": True,
                "supportedLocales": ["zh-CN", "en"],
                "defaultLocale": "zh-CN",
            },
        )
        value = request("GET", f"/clients/{identifier}/client-secret")["value"]
        secret_file = ROOT / "runtime/secrets/identity_admin_secret"
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
        config["identity_management"] = {
            "client_id": client_name,
            "client_secret_file": "../secrets/identity_admin_secret",
            "email_enabled": False,
        }
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(config, indent=2))
        temporary.replace(path)
    print("本机账号管理连接已配置；未变更已有员工密码或业务授权。")


if __name__ == "__main__":
    configure()
