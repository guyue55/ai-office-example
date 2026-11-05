"""临时真实Keycloak验收；仅创建合成账号，不读取或连接已有实例。"""

import asyncio
import json
import os
import secrets
import time
from uuid import uuid4

import httpx
import pytest
from app.common.errors import DomainError
from app.infrastructure.identity.keycloak import KeycloakAdmin
from test_query_postgres import docker

pytestmark = pytest.mark.live


@pytest.fixture
def isolated_identity(tmp_path):
    if os.environ.get("OFFICE_IDENTITY_CONTAINER_TESTS") != "1":
        pytest.skip("需OFFICE_IDENTITY_CONTAINER_TESTS=1，创建临时身份容器")
    realm = "isolated-office"
    setup_secret = secrets.token_urlsafe(32)
    management_secret = secrets.token_urlsafe(32)
    realm_file = tmp_path / "realm.json"
    realm_file.write_text(
        json.dumps(
            {
                "realm": realm,
                "enabled": True,
                "clients": [
                    {
                        "clientId": "setup",
                        "secret": setup_secret,
                        "enabled": True,
                        "serviceAccountsEnabled": True,
                        "standardFlowEnabled": False,
                        "fullScopeAllowed": True,
                    }
                ],
                "users": [
                    {
                        "username": "service-account-setup",
                        "enabled": True,
                        "serviceAccountClientId": "setup",
                        "clientRoles": {"realm-management": ["realm-admin"]},
                    }
                ],
            }
        )
    )
    realm_file.chmod(0o600)
    secret_file = tmp_path / "management-secret"
    secret_file.write_text(management_secret)
    secret_file.chmod(0o600)
    name = "office-identity-test-" + uuid4().hex[:12]
    try:
        docker(
            "run",
            "-d",
            "--pull=never",
            "--name",
            name,
            "-p",
            "127.0.0.1::8080",
            "-e",
            "JAVA_OPTS_KC_HEAP=-Xms128m -Xmx384m",
            "-v",
            f"{realm_file}:/opt/keycloak/data/import/realm.json:ro",
            "quay.io/keycloak/keycloak:26.8.1",
            "start-dev",
            "--import-realm",
        )
        port = docker("port", name, "8080/tcp").rsplit(":", 1)[1]
        issuer = f"http://127.0.0.1:{port}/realms/{realm}"
        with httpx.Client(timeout=5) as http:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    if http.get(issuer + "/.well-known/openid-configuration").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("临时身份服务未就绪；未访问既有实例")
            response = http.post(
                issuer + "/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": "setup",
                    "client_secret": setup_secret,
                },
            )
            if response.status_code != 200:
                raise RuntimeError("临时身份初始化授权失败")
            http.headers["Authorization"] = "Bearer " + response.json()["access_token"]
            base = f"http://127.0.0.1:{port}/admin/realms/{realm}"

            def request(method, path, **kwargs):
                result = http.request(method, base + path, **kwargs)
                if result.status_code not in {200, 201, 204}:
                    raise RuntimeError(f"临时身份初始化失败：HTTP {result.status_code}")
                return result.json() if result.content else None

            request(
                "POST",
                "/clients",
                json={
                    "clientId": "management",
                    "secret": management_secret,
                    "enabled": True,
                    "serviceAccountsEnabled": True,
                    "standardFlowEnabled": False,
                    "directAccessGrantsEnabled": False,
                    "fullScopeAllowed": False,
                },
            )
            identifier = request("GET", "/clients", params={"clientId": "management"})[0]["id"]
            account = request("GET", f"/clients/{identifier}/service-account-user")
            manager = request("GET", "/clients", params={"clientId": "realm-management"})[0]["id"]
            roles = [
                request("GET", f"/clients/{manager}/roles/{role}")
                for role in ("query-users", "view-users", "manage-users")
            ]
            request("POST", f"/users/{account['id']}/role-mappings/clients/{manager}", json=roles)
            request("POST", f"/clients/{identifier}/scope-mappings/clients/{manager}", json=roles)
            profile = request("GET", "/users/profile")
            for attribute in profile["attributes"]:
                if attribute["name"] in {"lastName", "email"}:
                    attribute.pop("required", None)
            profile["attributes"].append(
                {
                    "name": "officeOperation",
                    "permissions": {"view": ["admin"], "edit": ["admin"]},
                    "validations": {"length": {"max": 100}},
                }
            )
            request("PUT", "/users/profile", json=profile)
        yield issuer, secret_file
    finally:
        try:
            docker("rm", "-f", name)
        finally:
            realm_file.unlink(missing_ok=True)
            secret_file.unlink(missing_ok=True)


def test_real_account_lifecycle_and_management_scope(isolated_identity):
    issuer, secret_file = isolated_identity

    async def scenario():
        client = KeycloakAdmin(issuer, "management", secret_file)
        try:
            username = "isolated-" + uuid4().hex[:12]
            operation = str(uuid4())
            assert await client.find_username(username) is None
            account = await client.create(username, "隔离员工", "", operation)
            assert not account.enabled and account.operation_id == operation
            assert "UPDATE_PASSWORD" in account.actions
            matches = await client.search(username)
            assert [row.id for row in matches] == [account.id]
            assert await client.search("service-account-") == []
            with pytest.raises(DomainError) as duplicate:
                await client.create(username, "重复开户", "", str(uuid4()))
            assert duplicate.value.status == 409
            await client.update(account.id, name="隔离员工已更新")
            await client.temporary_password(account.id, secrets.token_urlsafe(24) + "Aa1!")
            await client.update(account.id, enabled=True)
            current = await client.get(account.id)
            assert current.enabled and current.name == "隔离员工已更新"
            assert current.operation_id == operation
            assert "UPDATE_PASSWORD" in current.actions
            await client.end_sessions(account.id)
            await client.update(account.id, enabled=False)
            assert not (await client.get(account.id)).enabled
            token = await client._access_token()
            for url in (
                client.base + "/clients",
                issuer.split("/realms/")[0] + "/admin/realms/master/users",
            ):
                response = await client.http.get(url, headers={"Authorization": "Bearer " + token})
                assert response.status_code == 403
        finally:
            await client.aclose()

    asyncio.run(scenario())
