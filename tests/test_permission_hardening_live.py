"""真实PG/API验证拆分权限，保留原员工资料与权限并在结束时恢复。"""

import asyncio
from uuid import uuid4

import pytest
from app.common.errors import DomainError
from app.modules.access.authentication import TokenVerifier
from conftest import browser_login
from office_client import ServiceError


def test_independent_audit_name_permission_and_denial_event(live):
    admin, employee = live["operator"], live["employee"]
    path = f"/api/v2/access/permission-sets/{live['bundle']['id']}/permissions"
    original = admin.request("GET", path)
    person = admin.access_detail("employees", live["employee_id"])
    original_name = person["name"]

    def grant(codes):
        current = admin.request("GET", path)
        admin.request("PUT", path, json={"version": current["version"], "permissions": codes})

    try:
        grant([*live["permissions"], "access.read", "employee.update"])
        assert employee.access_list("employees")["items"]
        with pytest.raises(ServiceError) as denied:
            employee.access_list("audit")
        assert denied.value.status == 403
        events = admin.access_list("audit", action="security.denied")
        assert any(row["actor_id"] == live["employee_id"] for row in events["items"])
        detail = admin.access_detail("employees", live["employee_id"])
        new_name = "权限测试-" + uuid4().hex[:8]
        employee.request(
            "PATCH",
            f"/api/v2/access/employees/{detail['id']}",
            json={
                "version": detail["version"],
                "name": new_name,
                "active": detail["active"],
            },
        )
        changed = admin.access_detail("employees", detail["id"])
        assert changed["name"] == new_name
        with pytest.raises(ServiceError) as denied:
            employee.request(
                "PATCH",
                f"/api/v2/access/employees/{detail['id']}",
                json={
                    "version": changed["version"],
                    "name": new_name,
                    "active": False,
                },
            )
        assert denied.value.status == 403
        with pytest.raises(ServiceError) as denied:
            employee.request("POST", "/api/v2/access/roles", json={"name": "禁止越权"})
        assert denied.value.status == 403
        grant([*live["permissions"], "audit.read"])
        assert employee.access_list("audit")["items"]
        with pytest.raises(ServiceError):
            employee.access_list("employees")
    finally:
        current = admin.access_detail("employees", live["employee_id"])
        admin.request(
            "PATCH",
            f"/api/v2/access/employees/{current['id']}",
            json={
                "version": current["version"],
                "name": original_name,
                "active": current["active"],
            },
        )
        grant(original["ids"])


def test_external_identity_logout_invalidates_online_token(live):
    from app.infrastructure.identity.keycloak import KeycloakAdmin

    settings = live["settings"]
    management = settings.identity_management
    assert management is not None
    isolated = browser_login("employee")
    raw = isolated.store.load()["access_token"]
    assert settings.token_introspection is not None
    verifier = TokenVerifier(settings)

    async def exercise():
        provider = KeycloakAdmin(
            settings.issuer, management.client_id, management.client_secret_file
        )
        try:
            principal = await verifier.verify_async(raw)
            await provider.end_session(principal.subject, principal.claims["sid"])
            # 旧JWT签名和有效期仍合法；在线身份状态已失效。
            assert verifier.verify(raw).subject == principal.subject
            with pytest.raises(DomainError) as denied:
                await verifier.verify_async(raw)
            assert denied.value.code == "invalid_token"
        finally:
            await provider.aclose()
            await verifier.aclose()

    asyncio.run(exercise())
