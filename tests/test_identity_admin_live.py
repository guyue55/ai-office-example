"""显式本机开发身份服务验证；不修改已有账号，不输出测试密码。"""

import asyncio
import secrets
from uuid import uuid4

from app.infrastructure.identity.keycloak import KeycloakAdmin


def test_real_identity_creation_and_scope(live):
    settings = live["settings"]
    config = settings.identity_management
    assert config is not None

    async def scenario():
        client = KeycloakAdmin(settings.issuer, config.client_id, config.client_secret_file)
        try:
            operation = str(uuid4())
            created = await client.create(
                "identity-check-" + uuid4().hex[:12], "身份适配验证", "", operation
            )
            assert not created.enabled and created.operation_id == operation
            assert "UPDATE_PASSWORD" in created.actions
            await client.update(created.id, name="身份适配验证已更新")
            assert (await client.get(created.id)).name == "身份适配验证已更新"
            await client.temporary_password(created.id, secrets.token_urlsafe(24) + "Aa1!")
            await client.update(created.id, enabled=True)
            assert (await client.get(created.id)).enabled
            await client.end_sessions(created.id)
            await client.update(created.id, enabled=False)
            token = await client._access_token()
            for url in (
                client.base + "/clients",
                "http://127.0.0.1:8081/admin/realms/master/users",
            ):
                response = await client.http.get(url, headers={"Authorization": "Bearer " + token})
                assert response.status_code == 403
        finally:
            await client.aclose()

    asyncio.run(scenario())
