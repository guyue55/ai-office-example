"""显式配置独立本机在线校验客户端；不变更员工、密码或现有客户端。"""

import json

import httpx
from dev_stack import ROOT, secret


def configure():
    path = ROOT / "runtime/foundation/config.json"
    config = json.loads(path.read_text())
    if config["issuer"] != "http://127.0.0.1:8081/realms/office":
        raise RuntimeError("仅允许独立本机开发realm")
    client_id = config["audience"]
    realm = "/admin/realms/office"
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
        response = http.get(realm + "/clients", params={"clientId": client_id})
        response.raise_for_status()
        clients = response.json()
        if not clients:
            http.post(
                realm + "/clients",
                json={
                    "clientId": client_id,
                    "enabled": True,
                    "publicClient": False,
                    "serviceAccountsEnabled": False,
                    "standardFlowEnabled": False,
                    "directAccessGrantsEnabled": False,
                    "fullScopeAllowed": False,
                    "protocol": "openid-connect",
                },
            ).raise_for_status()
            response = http.get(realm + "/clients", params={"clientId": client_id})
            response.raise_for_status()
            clients = response.json()
        client = clients[0]
        if any(
            client.get(key)
            for key in (
                "publicClient",
                "serviceAccountsEnabled",
                "standardFlowEnabled",
                "directAccessGrantsEnabled",
                "fullScopeAllowed",
            )
        ):
            raise RuntimeError("已有客户端不符合最小权限约束，未覆盖")
        response = http.get(realm + "/clients/" + client["id"] + "/client-secret")
        response.raise_for_status()
        secret_path = ROOT / "runtime/secrets/introspection_client"
        # 创建即限制权限；不输出秘密，也不轮换现有客户端秘密。
        import os

        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(response.json()["value"])
        secret_path.chmod(0o600)
        config["token_introspection"] = {
            "client_id": client_id,
            "client_secret_file": str(secret_path),
        }
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    print("独立在线校验客户端已配置；未授予用户管理或服务账号权限")


if __name__ == "__main__":
    configure()
