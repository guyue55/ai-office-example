"""显式迁址的配置与身份维护边界；全部使用构造数据。"""

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
OLD = "http://192.168.2.10:19880"
NEW = "http://172.16.2.10:19880"


def module(monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT / "deploy/installer"))
    spec = importlib.util.spec_from_file_location(
        "isolated_" + name, ROOT / "deploy/installer" / f"{name}.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def installed(tmp_path):
    root = tmp_path / "instance"
    root.mkdir()
    config = {
        "public_url": OLD,
        "listen_address": "192.168.2.10",
        "allow_lan_http_demo": True,
        "port": 19880,
    }
    values = {
        "installation.json": {
            "project": "office-" + hashlib.sha256(str(root).encode()).hexdigest()[:12],
            "settings": config,
        },
        "compose.json": {
            "services": {
                "postgres": {},
                "service": {"image": "old"},
                "setup": {"image": "old"},
                "gateway": {"ports": []},
                "identity": {"environment": {}},
            }
        },
        "service/service.json": {
            "public_url": OLD,
            "issuer": OLD + "/identity/realms/office",
            "audience": OLD + "/mcp",
            "token_introspection": {
                "client_id": OLD + "/mcp",
                "client_secret_file": "secrets/unchanged",
            },
        },
        "maintenance/owner.json": {
            "public_url": OLD,
            "issuer": OLD + "/identity/realms/office",
            "audience": OLD + "/mcp",
        },
        "gateway/nginx.conf": "original",
    }
    for name, value in values.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    return root


def test_plan_is_readonly_and_keeps_secret_references(tmp_path, monkeypatch):
    tool = module(monkeypatch, "relocate")
    root = installed(tmp_path)
    monkeypatch.setattr(tool, "command", lambda *a, **kw: b"sha256:candidate")
    original = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = tool.plan(root, NEW, "172.16.2.10", "candidate")
    assert result["files"]["service/service.json"]["issuer"] == NEW + "/identity/realms/office"
    assert (
        result["files"]["service/service.json"]["token_introspection"]["client_secret_file"]
        == "secrets/unchanged"
    )
    assert result["files"]["compose.json"]["services"]["gateway"]["ports"] == [
        "172.16.2.10:19880:19880"
    ]
    assert all(Path(p).read_bytes() == value for p, value in original.items())


def test_relocation_can_move_to_all_current_lan_addresses(tmp_path, monkeypatch):
    tool = module(monkeypatch, "relocate")
    root = installed(tmp_path)
    monkeypatch.setattr(tool, "command", lambda *a, **kw: b"sha256:candidate")
    result = tool.plan(root, NEW, "lan", "candidate")
    assert result["files"]["compose.json"]["services"]["gateway"]["ports"] == [
        "0.0.0.0:19880:19880"
    ]
    assert result["files"]["installation.json"]["settings"]["bind_mode"] == "lan"


@pytest.mark.parametrize(
    "url,listen",
    [
        (OLD, "192.168.2.10"),
        (NEW, "0.0.0.0"),
        ("http://172.16.2.10:19881", "172.16.2.10"),
        ("https://example.invalid", "172.16.2.10"),
    ],
)
def test_plan_refuses_scope_expansion(tmp_path, monkeypatch, url, listen):
    tool = module(monkeypatch, "relocate")
    root = installed(tmp_path)
    with pytest.raises(ValueError):
        tool.plan(root, url, listen, "unused")


@pytest.mark.parametrize("bad_mapper", [False, True])
def test_identity_migration_preserves_ids_and_requires_original_contract(
    tmp_path, monkeypatch, bad_mapper
):
    tool = module(monkeypatch, "relocation_phases")
    tool.ROOT = tmp_path
    (tmp_path / "maintenance").mkdir()
    (tmp_path / "maintenance/identity.json").write_text(json.dumps({"subject": "same-user"}))
    clients = [
        {"clientId": "office-cli", "id": "cli", "redirectUris": ["http://127.0.0.1:8765/callback"]},
        {"clientId": "office-web", "id": "web", "redirectUris": [OLD + "/auth/callback"]},
        {"clientId": OLD + "/mcp", "id": "same-resource"},
    ]
    calls = []

    def respond(request):
        path = request.url.path
        calls.append(
            (request.method, path, json.loads(request.content) if request.content else None)
        )
        if request.method != "GET":
            return httpx.Response(204)
        if path.endswith("/users/count"):
            return httpx.Response(200, json=3)
        if path.endswith("/users/same-user"):
            return httpx.Response(200, json={"id": "same-user"})
        if path.endswith("/clients"):
            return httpx.Response(200, json=clients)
        if path.endswith("/protocol-mappers/models"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "mapper",
                        "name": "office-audience",
                        "config": {
                            "included.custom.audience": "wrong" if bad_mapper else OLD + "/mcp"
                        },
                    }
                ],
            )
        return httpx.Response(200, json={"id": "same-realm", "attributes": {}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        if bad_mapper:
            with pytest.raises(RuntimeError):
                tool.identity_state(
                    http, "http://fixture/admin/realms/office", OLD, NEW, apply=True
                )
            assert not any(method != "GET" for method, _, _ in calls)
        else:
            result = tool.identity_state(
                http, "http://fixture/admin/realms/office", OLD, NEW, apply=True
            )
            assert result["realm"] == "same-realm" and result["users"] == 3
            changes = [
                body
                for method, path, body in calls
                if method == "PUT" and path.endswith("/same-resource")
            ]
            assert changes[0]["id"] == "same-resource" and changes[0]["clientId"] == NEW + "/mcp"
            assert any(
                method == "POST" and path.endswith("/logout-all") for method, path, _ in calls
            )
            assert all("/reset-password" not in path for _, path, _ in calls)


def test_failed_preflight_does_not_stop_services(tmp_path, monkeypatch):
    tool = module(monkeypatch, "relocate")
    root = installed(tmp_path)
    monkeypatch.setattr(tool, "write", lambda p, v, *a: p.write_text(json.dumps(v)))

    def fail(*a, **kw):
        raise RuntimeError("blocked")

    monkeypatch.setattr(tool, "phase", fail)
    calls = []
    monkeypatch.setattr(tool, "command", lambda args, **kw: calls.append(args))
    with pytest.raises(RuntimeError):
        tool.apply(root, {"old": OLD, "new": NEW, "project": "isolated"})
    assert calls == [] and not (root / ".relocation-pending.json").exists()


@pytest.mark.parametrize("phase,reopened", [("verified", False), ("failed", True)])
def test_restore_refuses_opened_or_completed_instance(tmp_path, monkeypatch, phase, reopened):
    tool = module(monkeypatch, "relocate")
    root = installed(tmp_path)
    run_id = "00000000-0000-4000-8000-000000000001"
    (root / ".relocation-pending.json").write_text(
        json.dumps({"id": run_id, "phase": phase, "write_reopened": reopened})
    )
    calls = []
    monkeypatch.setattr(tool, "command", lambda args, **kw: calls.append(args))
    with pytest.raises(ValueError):
        tool.restore(root, run_id)
    assert calls == []


def test_apply_verifies_behind_maintenance_gate_before_opening(tmp_path, monkeypatch):
    tool = module(monkeypatch, "relocate")
    root = installed(tmp_path)
    monkeypatch.setattr(tool, "command", lambda *a, **kw: b"sha256:candidate")
    info = tool.plan(root, NEW, "172.16.2.10", "candidate")
    monkeypatch.setattr(
        tool, "write", lambda p, v, *a: p.write_text(json.dumps(v) if isinstance(v, dict) else v)
    )
    monkeypatch.setattr(tool, "phase", lambda *a, **kw: {"verified": "same-baseline"})
    monkeypatch.setattr(tool, "dump_databases", lambda *a: None)
    states = []

    def run(args, **kwargs):
        if args[-1] == "verify":
            conf = (root / "gateway/nginx.conf").read_text()
            states.append("return 503;" in conf)
            if "return 503;" in conf:
                pattern = re.search(r'if \(\$uri !~ "([^"]+)"\)', conf).group(1)
                assert re.fullmatch(pattern, "/health/ready")
                assert re.fullmatch(
                    pattern, "/identity/realms/office/.well-known/openid-configuration"
                )
                assert not re.fullmatch(pattern, "/api/v2/records")
                assert not re.fullmatch(
                    pattern, "/identity/realms/office/protocol/openid-connect/auth"
                )
        return b""

    monkeypatch.setattr(tool, "command", run)
    tool.apply(root, info)
    assert states == [True, False]
    assert not (root / ".relocation-pending.json").exists()
    assert json.loads((root / "installation.json").read_text())["settings"]["public_url"] == NEW
