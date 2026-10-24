"""策略只触及已列明的期限字段；不使用真实管理凭证或外部网络。"""

import json
import sys
from pathlib import Path

import httpx
import pytest
from office_client.product import PRODUCT

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configure_session_policy import validate_shape, verify_evidence
from session_policy import apply_policy, policy, read_policy


def test_parent_and_web_updates_are_ordered_and_other_attributes_preserved():
    realm = {**policy()["realm"], "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 36000}
    clients = {
        name: {"id": name, "clientId": name, "attributes": {"unchanged": "yes"}}
        for name in policy()["clients"]
    }
    mutations = []

    def handle(request):
        path = request.url.path
        if request.method == "GET" and path.endswith("/clients"):
            return httpx.Response(200, json=[clients[request.url.params["clientId"]]])
        if request.method == "GET":
            return httpx.Response(200, json=realm)
        body = json.loads(request.content)
        if "/clients/" in path:
            values = body["attributes"]
            # 重现实际平台约束：不能先把子期限提高到父期限之外。
            if int(values.get("client.session.max.lifespan", 0)) > realm["ssoSessionMaxLifespan"]:
                return httpx.Response(400)
            clients[body["id"]] = body
            mutations.append(body["id"])
        else:
            realm.update(body)
            mutations.append("realm")
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        apply_policy(http, {}, policy())
        assert read_policy(http, {}) == policy()
    assert mutations == [
        PRODUCT["web_client_id"],
        "realm",
        PRODUCT["client_id"],
        PRODUCT["web_client_id"],
    ]
    assert all(v["attributes"]["unchanged"] == "yes" for v in clients.values())


@pytest.mark.parametrize("field", ["roles", "users", "realm"])
def test_policy_rejects_extra_realm_fields(field):
    desired = policy()
    desired["realm"][field] = "not-allowed"
    with pytest.raises(RuntimeError):
        validate_shape(desired)


def test_policy_rejects_other_client():
    desired = policy()
    desired["clients"]["another-client"] = {}
    with pytest.raises(RuntimeError):
        validate_shape(desired)


def test_unverified_evidence_cannot_apply_policy(tmp_path, monkeypatch):
    import configure_session_policy as module

    source = tmp_path / "failed.json"
    source.write_text(json.dumps({"status": "failed"}))
    monkeypatch.setattr(module, "runtime_file", lambda path: source)
    with pytest.raises(RuntimeError, match="验证未通过"):
        verify_evidence(source)
