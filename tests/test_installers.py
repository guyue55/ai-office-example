"""部署边界与交付包负向测试；不依赖本机数据库、身份或凭证。"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def delivery_import_path(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    monkeypatch.syspath_prepend(str(ROOT / "deploy/installer"))


def module(relative):
    spec = importlib.util.spec_from_file_location("installer_test", ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize(
    "url",
    [
        "http://office.example",
        "https://a:b@office.example",
        "https://office.example/sub",
        "https://office.example/?token=x",
    ],
)
def test_server_rejects_unsafe_public_address(url):
    layout = module("deploy/installer/layout.py")
    with pytest.raises(ValueError):
        layout.settings({"public_url": url, "listen_address": "192.168.1.10"})


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "::1"])
def test_server_rejects_unsafe_listen(address):
    layout = module("deploy/installer/layout.py")
    with pytest.raises(ValueError):
        layout.settings({"public_url": "https://office.example", "listen_address": address})


@pytest.mark.parametrize(
    "listen,expected_mode,expected_address",
    [
        (None, "lan", "0.0.0.0"),
        ("lan", "lan", "0.0.0.0"),
        ("local", "local", "127.0.0.1"),
        ("192.168.1.10", "address", "192.168.1.10"),
    ],
)
def test_server_bind_modes_are_explicit(listen, expected_mode, expected_address):
    layout = module("deploy/installer/layout.py")
    value = {"public_url": "https://office.example"}
    if listen is not None:
        value["listen_address"] = listen
    config = layout.settings(value)
    assert config["bind_mode"] == expected_mode
    assert config["listen_address"] == expected_address


def test_https_public_address_can_use_certificate_ip():
    layout = module("deploy/installer/layout.py")
    config = layout.settings({"public_url": "https://192.168.1.10", "listen_address": "lan"})
    assert config["hostname"] == "192.168.1.10"


def test_compose_only_exposes_gateway_and_separates_maintenance():
    layout = module("deploy/installer/layout.py")
    config = layout.settings({"public_url": "https://office.example", "listen_address": "lan"})
    services = layout.compose(
        "/srv/instance", "/opt/source", config, "sha256:candidate", "isolated"
    )["services"]
    assert [name for name, service in services.items() if service.get("ports")] == ["gateway"]
    assert services["gateway"]["ports"] == ["0.0.0.0:443:443"]
    assert services["setup"]["profiles"] == ["setup"]
    assert all("maintenance" not in row["source"] for row in services["service"]["volumes"])
    assert services["service"]["read_only"]
    assert "/identity/" in layout.nginx(config)
    assert "access_log off" in layout.nginx(config)
    assert (
        'if ($host != "office.example") { return 308 https://office.example$request_uri; }'
        in layout.nginx(config)
    )


def test_existing_directory_refused_before_secret_write(tmp_path):
    layout = module("deploy/installer/layout.py")
    (tmp_path / "foreign").write_text("keep")
    with pytest.raises(ValueError, match="拒绝接管"):
        layout.prepare(
            tmp_path,
            str(tmp_path),
            "/source",
            {"public_url": "https://office.example", "listen_address": "192.168.1.10"},
            "candidate",
            999,
            999,
            1000,
            0,
        )
    assert (tmp_path / "foreign").read_text() == "keep"
    assert not (tmp_path / ".preparation.json").exists()


@pytest.mark.parametrize("name", ["../other", "/etc/passwd", "C:\\secret", "nested/../../other"])
def test_pc_bundle_rejects_escape(tmp_path, name):
    setup = module("installers/client/setup_client.py")
    (tmp_path / "bundle.json").write_text(
        json.dumps({"format": 1, "service": "https://office.example", "files": {name: "bad"}})
    )
    with pytest.raises(ValueError):
        setup.load_bundle(tmp_path)


def test_pc_bundle_rejects_tamper(tmp_path):
    setup = module("installers/client/setup_client.py")
    (tmp_path / "bundle.json").write_text(
        json.dumps(
            {"format": 1, "service": "https://office.example", "files": {"setup.ps1": "0" * 64}}
        )
    )
    (tmp_path / "setup.ps1").write_text("tampered")
    with pytest.raises(ValueError, match="摘要"):
        setup.load_bundle(tmp_path)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_pc_bundle_rejects_ambiguous_agent_mode(tmp_path, value):
    setup = module("installers/client/setup_client.py")
    (tmp_path / "bundle.json").write_text(json.dumps({"agent_runtime": value}))
    with pytest.raises(ValueError, match="Agent安装标记"):
        setup.load_bundle(tmp_path)


def test_pc_scripts_preserve_global_environment():
    source = (ROOT / "installers/client/setup.ps1").read_text()
    assert "Get-FileHash" in source and "0.9.18" in source
    assert "Set-ExecutionPolicy" not in source and "-ExecutionPolicy" not in source
    assert "tool install" not in source


def test_pc_rejects_state_directory_escape(tmp_path):
    setup = module("installers/client/setup_client.py")
    root = tmp_path / "delivery"
    root.mkdir()
    (root / "bundle.json").write_text(
        json.dumps(
            {
                "format": 1,
                "service": "https://office.example",
                "files": {
                    "empty.txt": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                },
            }
        )
    )
    (root / "empty.txt").write_bytes(b"")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".office").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="状态目录"):
        setup.install(root, "must-not-execute")
    assert not list(outside.iterdir())


@pytest.mark.parametrize(
    "url", ["http://10.2.3.4:8800", "http://172.16.1.2:8800", "http://192.168.2.3:8800"]
)
def test_explicit_demo_allows_only_private_ip(tmp_path, url):
    from app.infrastructure.config import Settings
    from office_client.auth import ClientError, trusted_url

    layout = module("deploy/installer/layout.py")
    with pytest.raises(ClientError):
        trusted_url(url)
    assert trusted_url(url, allow_lan_http_demo=True) == url
    settings = Settings(
        database_user="test",
        database_password_file=tmp_path / "secret",
        public_url=url,
        issuer=url + "/identity/realms/office",
        audience=url + "/mcp",
        allow_lan_http_demo=True,
    )
    assert settings.allow_lan_http_demo
    config = layout.settings(
        {
            "public_url": url,
            "listen_address": url.split("/")[2].split(":")[0],
            "allow_lan_http_demo": True,
        }
    )
    assert "ssl_certificate" not in layout.nginx(config)
    gateway = layout.compose("/instance", "/source", config, "candidate", "test")["services"][
        "gateway"
    ]
    assert len(gateway["volumes"]) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8:8800",
        "http://example.com",
        "http://[::1]",
        "http://100.64.0.1",
        "file:///etc/passwd",
    ],
)
def test_demo_flag_does_not_allow_public_or_non_http(url):
    from office_client.auth import ClientError, trusted_url

    with pytest.raises(ClientError):
        trusted_url(url, allow_lan_http_demo=True, loopback_http=False)


def test_env_parser_rejects_execution_duplicate_and_unknown(tmp_path):
    import configparser

    from delivery_config import read_env

    config = tmp_path / "delivery.env"
    for body in (
        "OFFICE_PUBLIC_URL=$(touch /tmp/not-allowed)",
        "OFFICE_PUBLIC_URL=http://10.0.0.1\nOFFICE_PUBLIC_URL=http://10.0.0.2",
        "PASSWORD=secret",
    ):
        config.write_text(body, encoding="utf-8")
        with pytest.raises((ValueError, configparser.Error)):
            read_env(config)
    config.write_text(
        'OFFICE_DEPLOYMENT_MODE=lan-http-demo\nOFFICE_PUBLIC_URL="http://192.168.1.5:8800"\n',
        encoding="utf-8-sig",
    )
    assert read_env(config)["OFFICE_PUBLIC_URL"] == "http://192.168.1.5:8800"


def test_server_still_requires_explicit_demo(tmp_path):
    from app.infrastructure.config import Settings

    with pytest.raises(ValueError):
        Settings(
            database_user="test",
            database_password_file=tmp_path / "secret",
            public_url="http://192.168.1.5",
            issuer="http://192.168.1.5/identity/realms/office",
            audience="http://192.168.1.5/mcp",
        )


def test_delivery_defaults_to_lan_binding_and_accepts_certificate_ip(tmp_path):
    from delivery_config import read_env, server_settings

    values = server_settings(
        {
            "OFFICE_PUBLIC_URL": "https://office.example",
            "OFFICE_DEPLOYMENT_MODE": "https",
        }
    )
    assert values["listen_address"] == "lan"
    path = tmp_path / "delivery.env"
    path.write_text("OFFICE_DEPLOYMENT_MODE=https\nOFFICE_PUBLIC_URL=https://192.168.1.10\n")
    assert read_env(path)["OFFICE_PUBLIC_URL"] == "https://192.168.1.10"


def test_editable_connection_preserves_package_integrity(tmp_path):
    import hashlib

    setup = module("installers/client/setup_client.py")
    content = b"immutable"
    (tmp_path / "program.py").write_bytes(content)
    (tmp_path / "bundle.json").write_text(
        json.dumps(
            {
                "format": 1,
                "service": "https://old.example",
                "files": {"program.py": hashlib.sha256(content).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "OFFICE_DEPLOYMENT_MODE=lan-http-demo\nOFFICE_PUBLIC_URL=http://192.168.1.8:8800\n",
        encoding="utf-8",
    )
    assert setup.load_bundle(tmp_path)["service"] == "http://192.168.1.8:8800"
    (tmp_path / "program.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="摘要"):
        setup.load_bundle(tmp_path)


@pytest.mark.parametrize("flag", ["false", "true", 1])
def test_demo_flag_requires_actual_boolean(flag):
    from office_client.auth import ClientError, trusted_url

    with pytest.raises(ClientError, match="布尔"):
        trusted_url("http://192.168.1.5", allow_lan_http_demo=flag)


def test_pc_rejects_generated_connection_symlink(tmp_path, monkeypatch):
    setup = module("installers/client/setup_client.py")
    monkeypatch.setattr(setup, "load_bundle", lambda root: {})
    state = tmp_path / ".office"
    state.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("preserve")
    (state / "connection.json").symlink_to(outside)
    with pytest.raises(ValueError, match="状态文件"):
        setup.install(tmp_path, "must-not-execute")
    assert outside.read_text() == "preserve"


@pytest.fixture
def external_database():
    return {
        "host": "database.internal",
        "port": 5432,
        "name": "office",
        "owner": "office_owner",
        "user": "office_app",
        "identity_name": "office_identity",
        "identity_user": "office_identity",
        "owner_password_file": "/secure/owner",
        "app_password_file": "/secure/app",
        "identity_password_file": "/secure/identity",
    }


def test_external_database_omits_container_and_volume(external_database):
    layout = module("deploy/installer/layout.py")
    cfg = layout.settings(
        {
            "public_url": "https://office.example",
            "listen_address": "192.168.1.10",
            "external_database": external_database,
        }
    )
    result = layout.compose("/instance", "/source", cfg, "image", "test", {"postgres": "unused"})
    assert "postgres" not in result["services"]
    assert not result["volumes"]
    identity = result["services"]["identity"]
    assert "depends_on" not in identity
    assert (
        identity["environment"]["KC_DB_URL"]
        == "jdbc:postgresql://database.internal:5432/office_identity"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("host", "a/b"),
        ("port", 0),
        ("owner", "office_app"),
        ("user", "custom_runtime"),
        ("identity_name", "office"),
        ("app_password_file", "relative"),
    ],
)
def test_external_database_rejects_unsafe_configuration(external_database, key, value):
    database = module("deploy/installer/database.py")
    external_database[key] = value
    with pytest.raises(ValueError):
        database.validate_external(external_database)


def test_external_database_incomplete_never_falls_back():
    from delivery_config import server_settings

    with pytest.raises(ValueError, match="不会回退"):
        server_settings(
            {
                "OFFICE_PUBLIC_URL": "https://office.example",
                "OFFICE_DEPLOYMENT_MODE": "https",
                "OFFICE_DATABASE_HOST": "database.internal",
            }
        )


@pytest.fixture
def public_bundle(tmp_path):
    import hashlib

    root = tmp_path / "old"
    root.mkdir()
    data = b"public program"
    (root / "setup.sh").write_bytes(data)
    (root / ".env").write_text("OFFICE_PUBLIC_URL=https://old.example\n")
    (root / "bundle.json").write_text(
        json.dumps(
            {
                "format": 1,
                "connection_config": ".env",
                "service": "https://old.example",
                "files": {"setup.sh": hashlib.sha256(data).hexdigest()},
            }
        )
    )
    (root / ".office").mkdir()
    (root / ".office/private-state").write_text("never-copy")
    return root


def test_missing_editable_connection_is_actionable(public_bundle):
    setup = module("installers/client/setup_client.py")
    (public_bundle / ".env").unlink()
    with pytest.raises(ValueError, match="缺少服务连接"):
        setup.load_bundle(public_bundle)


def test_new_connection_stages_only_verified_public_files(public_bundle):
    setup = module("installers/client/setup_client.py")
    destination = public_bundle.parent / "new"
    result = setup.stage_connection(public_bundle, destination, "https://new.example")
    assert not result["login_migrated"]
    assert not (destination / ".office").exists()
    assert (public_bundle / ".office/private-state").read_text() == "never-copy"
    assert setup.load_bundle(destination)["service"] == "https://new.example"
    assert setup.load_bundle(public_bundle)["service"] == "https://old.example"
    with pytest.raises(ValueError):
        setup.stage_connection(public_bundle, destination, "https://another.example")


def test_failed_connection_staging_does_not_publish_partial_directory(public_bundle):
    setup = module("installers/client/setup_client.py")
    destination = public_bundle.parent / "new"
    with pytest.raises(ValueError):
        setup.stage_connection(
            public_bundle, destination, "http://8.8.8.8", allow_lan_http_demo=True
        )
    assert not destination.exists()


def test_delivery_rejects_zero_port(tmp_path):
    from delivery_config import read_env

    path = tmp_path / "config.env"
    path.write_text("OFFICE_PUBLIC_URL=https://office.example:0\n")
    with pytest.raises(ValueError, match="端口"):
        read_env(path)


@pytest.fixture
def database_snapshot():
    return {
        "heads": ["head"],
        "tables": ["employee", "permission"],
        "permissions": ["record.create"],
        "employees": 1,
        "revision": 1,
        "forbidden_privileges": [False],
    }


def test_database_snapshot_verifies_requirements_and_allows_extensions(database_snapshot):
    database = module("deploy/installer/database.py")
    result = database.validate_snapshot(
        database_snapshot,
        expected_head="head",
        expected_tables=["employee"],
        expected_permissions=["record.create"],
    )
    assert result["runtime_privileges_verified"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("heads", ["old"]),
        ("tables", []),
        ("permissions", []),
        ("employees", 0),
        ("revision", None),
        ("forbidden_privileges", [True]),
    ],
)
def test_database_snapshot_blocks_incomplete_or_overprivileged_installation(
    database_snapshot, key, value
):
    database_snapshot[key] = value
    with pytest.raises(ValueError):
        module("deploy/installer/database.py").validate_snapshot(
            database_snapshot,
            expected_head="head",
            expected_tables=["employee"],
            expected_permissions=["record.create"],
        )


def test_external_owner_preflight_matches_actual_migration_contract(external_database):
    external_database["owner"] = "other_owner"
    with pytest.raises(ValueError, match="office_owner"):
        module("deploy/installer/database.py").validate_external(external_database)


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"ok":true}',
        '{"ok":1,"service":"https://s.example","online_verified":true,"identity_verified":false,"credential_io":false}',
    ],
)
def test_zero_exit_alone_cannot_claim_connection_verified(payload):
    from types import SimpleNamespace

    verified, issue = module("installers/client/setup_client.py").parse_connection_report(
        SimpleNamespace(returncode=0, stdout=payload), "https://s.example"
    )
    assert not verified and issue["code"] == "invalid_check_result"


def test_installer_preserves_safe_connection_failure_details():
    from types import SimpleNamespace

    report = {
        "ok": False,
        "issue": {
            "code": "service_not_ready",
            "stage": "service_ready",
            "http_status": 503,
            "message": "服务尚未就绪",
            "upstream_body": "must-not-copy",
        },
    }
    verified, issue = module("installers/client/setup_client.py").parse_connection_report(
        SimpleNamespace(returncode=1, stdout=json.dumps(report)), "https://s.example"
    )
    assert not verified and issue["http_status"] == 503
    assert "upstream_body" not in issue


def test_verify_waits_for_restarting_identity_without_weakening_issuer(tmp_path, monkeypatch):
    import database
    import httpx

    server = module("deploy/installer/server.py")
    (tmp_path / "service").mkdir()
    issuer = "http://192.168.1.10/identity/realms/office"
    (tmp_path / "service/service.json").write_text(
        json.dumps({"public_url": "http://192.168.1.10", "issuer": issuer})
    )
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(database, "verify_database", lambda root: {"checked": True})
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)
    attempts = []

    def respond(request):
        if request.url.host == "identity":
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(503)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": issuer})
        return httpx.Response(200)

    client = httpx.Client
    monkeypatch.setattr(
        server.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(server, "write", lambda path, value, uid, gid: None)
    server.verify_phase()
    assert len(attempts) == 2


def test_new_install_generates_independent_model_key_once(tmp_path, monkeypatch):
    """仅写本轮临时目录；不创建容器或接触已有实例。"""
    import base64

    from app.infrastructure.config import Settings

    layout = module("deploy/installer/layout.py")
    monkeypatch.setattr(layout.os, "chown", lambda *args: None)
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if str(path) == "/input/database-init.sh":
            return "# synthetic installer fixture\n"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    options = {
        "public_url": "http://192.168.1.10:18880",
        "listen_address": "192.168.1.10",
        "allow_lan_http_demo": True,
    }
    args = (
        tmp_path,
        "/synthetic/instance",
        "/synthetic/source",
        options,
        "synthetic-candidate",
        999,
        999,
        1000,
        0,
    )
    layout.prepare(*args)
    key = tmp_path / "service/secrets/model_encryption_key"
    original = key.read_bytes()
    assert len(base64.urlsafe_b64decode(original)) == 32
    assert not key.stat().st_mode & 0o077
    assert original != (tmp_path / "service/secrets/web_session_secret").read_bytes()
    config = Settings.load(str(tmp_path / "service/service.json"))
    assert config.agent_secret_key_file == key
    assert config.agent_model_urls == []
    layout.prepare(*args)
    assert key.read_bytes() == original  # 重入安装绝不换掉已生成的根密钥。


def test_image_selection_unifies_file_and_environment(tmp_path):
    from delivery_config import image_selection, read_env

    path = tmp_path / "delivery.env"
    path.write_text(
        "OFFICE_PUBLIC_URL=https://office.example\nOFFICE_AGENT=1\nOFFICE_IMAGE=example/service:agent\n"
    )
    values = read_env(path)
    assert image_selection(values, {}) == {
        "OFFICE_AGENT": "1",
        "OFFICE_IMAGE": "example/service:agent",
    }
    assert image_selection({}, {})["OFFICE_AGENT"] == "0"
    for environment in [{"OFFICE_AGENT": "0"}, {"OFFICE_IMAGE": "other/image"}]:
        with pytest.raises(ValueError, match="冲突"):
            image_selection(values, environment)
    for value in ["2", "true", "-1"]:
        with pytest.raises(ValueError):
            image_selection({"OFFICE_AGENT": value}, {})


def test_instance_image_metadata_updates_compose_and_marker_together(tmp_path):
    root = tmp_path / "instance"
    root.mkdir()
    old_image = "sha256:" + "1" * 64
    new_image = "sha256:" + "2" * 64
    installation = root / "installation.json"
    compose = root / "compose.json"
    installation.write_text(json.dumps({"format": 1, "project": "office-test", "image": old_image}))
    compose.write_text(
        json.dumps(
            {
                "services": {
                    "service": {"image": old_image},
                    "setup": {"image": old_image},
                }
            }
        )
    )
    installation.chmod(0o600)
    compose.chmod(0o600)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy/installer/set_instance_image.py"),
            str(root),
            "--image",
            new_image,
            "--status",
            "healthy",
            "--from-image",
            old_image,
            "--backup",
            "/secure/backup",
            "--migration",
            "0017",
            "--commit",
            "abc123",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    marker = json.loads(installation.read_text())
    services = json.loads(compose.read_text())["services"]
    assert marker["image"] == new_image
    assert marker["last_upgrade"]["status"] == "healthy"
    assert services["service"]["image"] == new_image
    assert services["setup"]["image"] == new_image
    assert installation.stat().st_mode & 0o077 == 0
    assert compose.stat().st_mode & 0o077 == 0


def test_bootstrap_image_selection_does_not_execute_env(tmp_path):
    config = tmp_path / "delivery.env"
    config.write_text(
        "OFFICE_PUBLIC_URL=https://office.example.invalid\nOFFICE_AGENT=1\nOFFICE_IMAGE=ghcr.io/example/service:1-agent\n"
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/delivery_config.py"), str(config)],
        env={**os.environ, "OFFICE_AGENT": "", "OFFICE_IMAGE": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["1", "ghcr.io/example/service:1-agent"]
    config.write_text("OFFICE_AGENT=$(touch injected)\n")
    rejected = subprocess.run(
        [sys.executable, str(ROOT / "scripts/delivery_config.py"), str(config)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 2 and not (tmp_path / "injected").exists()
