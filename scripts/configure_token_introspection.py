"""显式配置独立本机在线校验客户端；不变更员工、密码或现有客户端。"""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dev_stack import ROOT, secret


def configure(path=None):
    path = Path(path or ROOT / "runtime/foundation/config.json").resolve()
    config = json.loads(path.read_text())
    issuer = urlsplit(config["issuer"])
    if (
        issuer.scheme != "http"
        or issuer.hostname != "127.0.0.1"
        or issuer.path != "/realms/office"
        or issuer.query
        or issuer.fragment
        or issuer.username
        or issuer.password
    ):
        raise RuntimeError("仅允许独立本机开发realm")
    client_id = config["audience"]
    realm = "/admin/realms/office"
    with httpx.Client(
        base_url=f"http://{issuer.netloc}", timeout=10, follow_redirects=False, trust_env=False
    ) as http:
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
        if len(clients) != 1:
            raise RuntimeError("校验客户端数量异常，未覆盖")
        client = clients[0]
        if not client.get("enabled") or client.get("protocol") != "openid-connect":
            raise RuntimeError("校验客户端状态异常，未覆盖")
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
        value = response.json()["value"]
        probe = http.post(
            realm.replace("/admin", "") + "/protocol/openid-connect/token/introspect",
            data={
                "token": "office-initialization-invalid-token",
                "client_id": client_id,
                "client_secret": value,
            },
        )
        probe.raise_for_status()
        if probe.json().get("active") is not False:
            raise RuntimeError("在线校验探针响应异常，未更新配置")
        # 使用当前配置专属文件，避免覆盖其他配置引用的旧秘密。
        secret_path = path.with_name(path.stem + ".introspection.secret")
        if secret_path.is_symlink():
            raise RuntimeError("秘密文件不能为符号链接")
        if not secret_path.exists() or secret_path.read_text() != value:
            temporary = secret_path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write(value)
            temporary.replace(secret_path)
        secret_path.chmod(0o600)
        desired = {"client_id": client_id, "client_secret_file": str(secret_path)}
        if config.get("token_introspection") != desired:
            config["token_introspection"] = desired
            temporary = path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(path)
    print("独立在线校验客户端已配置；未授予用户管理或服务账号权限")


if __name__ == "__main__":
    configure()
