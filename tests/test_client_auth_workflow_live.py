"""真实本机Keycloak：可见URL的同机PKCE仍按正常授权验证当前员工。"""

from conftest import browser_login


def test_no_browser_login_real_identity_and_local_metadata(live):
    client = browser_login("employee", no_browser=True)
    try:
        status = client.auth_status()
        assert status["credential_state"] == "present"
        assert status["online_verified"] is False
        assert client.me()["id"] == live["employee_id"]
    finally:
        client.logout()
        client.close()
