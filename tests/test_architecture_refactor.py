"""结构整改的公共行为反例：契约生成不依赖配置，且只读检查能发现漂移。"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export(*args, cwd=ROOT):
    env = {k: v for k, v in os.environ.items() if k != "OFFICE_CONFIG"}
    return subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/export_contracts.py"), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_export_is_independent_of_config_and_cwd_and_check_is_read_only(tmp_path):
    result = export("--output-root", str(tmp_path), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    files = [
        tmp_path / name
        for name in (
            "contracts/openapi.json",
            "contracts/business-codes.json",
            "clients/python/src/office_client/business_codes.json",
        )
    ]
    before = {p: p.read_bytes() for p in files}
    checked = export("--check", "--output-root", str(tmp_path), cwd=tmp_path)
    assert checked.returncode == 0, checked.stderr
    files[0].write_text("{}\n")
    failed = export("--check", "--output-root", str(tmp_path), cwd=tmp_path)
    assert failed.returncode != 0
    assert files[0].read_text() == "{}\n"
    assert all(p.read_bytes() == value for p, value in before.items() if p != files[0])


def test_relative_secret_paths_resolve_before_validation_from_config_directory(
    tmp_path, monkeypatch
):
    import json

    from app.infrastructure.config import Settings

    config_dir = tmp_path / "instance"
    secret_dir = config_dir / "secrets"
    secret_dir.mkdir(parents=True)
    web_key = secret_dir / "web"
    web_key.write_bytes(b"isolated-web-key-32-bytes-for-test-only")
    web_key.chmod(0o600)
    (secret_dir / "db").write_text("test-only-not-real-credential")
    file = config_dir / "service.json"
    file.write_text(
        json.dumps(
            {
                "database_user": "office_app",
                "database_password_file": "secrets/db",
                "web_session_secret_file": "secrets/web",
                "issuer": "https://identity.invalid",
                "audience": "https://service.invalid/mcp",
                "public_url": "https://service.invalid",
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    settings = Settings.load(str(file))
    assert settings.database_password_file == secret_dir / "db"
    assert settings.web_session_secret_file == web_key
    assert settings.web_session_secret == web_key.read_bytes()


def test_export_schema_matches_real_runtime_routes_without_database(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.entrypoints.http.schema import public_schema
    from app.infrastructure.config import Settings
    from app.main import create_app

    monkeypatch.setattr("app.main.async_sessions", lambda settings: SimpleNamespace())
    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "does-not-exist",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    assert public_schema() == create_app(settings).openapi()


def test_structure_checker_rejects_reverse_and_lazy_cyclic_dependencies(tmp_path):
    import runpy

    inspect = runpy.run_path(str(ROOT / "scripts/check_architecture.py"))["inspect"]
    app = tmp_path / "office-data-service/app"
    (app / "common").mkdir(parents=True)
    (app / "modules/access").mkdir(parents=True)
    (app / "common/errors.py").write_text(
        "def delayed():\n    from app.modules.access import schemas\n"
    )
    (app / "modules/access/schemas.py").write_text(
        "from app.common import errors\nimport sqlalchemy\n"
    )
    problems = inspect(tmp_path)
    assert any("循环依赖" in item for item in problems)
    assert any("app.common.errors 不得依赖 app.modules.access" in item for item in problems)
    assert any("sqlalchemy" in item for item in problems)


def test_independent_package_checker_rejects_server_imports(tmp_path):
    import runpy

    inspect = runpy.run_path(str(ROOT / "scripts/check_architecture.py"))["inspect"]
    package = tmp_path / "clients/python/src/office_client"
    package.mkdir(parents=True)
    (package / "client.py").write_text("from app.modules.access import schemas\n")
    assert any("独立包不得依赖 app" in item for item in inspect(tmp_path))


def test_actual_repository_respects_structure_rules():
    import runpy

    inspect = runpy.run_path(str(ROOT / "scripts/check_architecture.py"))["inspect"]
    assert inspect(ROOT) == []


def test_input_output_and_mcp_tool_registrations_are_complete():
    import asyncio
    from types import SimpleNamespace

    from app.application.commands import OPERATION_INPUTS
    from app.application.contracts import OPERATION_OUTPUTS
    from app.entrypoints.mcp.server import build_mcp
    from app.entrypoints.mcp.validation import TOOL_INPUTS

    assert set(OPERATION_INPUTS) == set(OPERATION_OUTPUTS) | {"access.list"}
    application = SimpleNamespace(
        settings=SimpleNamespace(
            issuer="https://identity.invalid", audience="https://service.invalid/mcp"
        )
    )
    server, _ = build_mcp(application)
    assert {tool.name for tool in asyncio.run(server.list_tools())} == set(TOOL_INPUTS)


def test_contract_publication_restores_prior_files_after_write_failure(tmp_path, monkeypatch):
    import runpy

    publish = runpy.run_path(str(ROOT / "scripts/export_contracts.py"))["publish"]
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text("old-first")
    second.write_text("old-second")
    replace = os.replace

    def fail_second(source, target):
        if Path(target) == second:
            raise OSError("isolated-disk-failure")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second)
    import pytest

    with pytest.raises(OSError):
        publish(tmp_path, {"first.json": "new-first", "second.json": "new-second"})
    assert first.read_text() == "old-first" and second.read_text() == "old-second"
    assert not list(tmp_path.glob(".contract-*"))


def test_config_and_module_contracts_import_without_database_or_protocol_modules(tmp_path):
    result = subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            "-c",
            "import sys; import app.infrastructure.config; import app.modules.access.outputs; import app.modules.data.outputs; assert not any(name in sys.modules for name in ('sqlalchemy','fastapi','mcp','httpx','psycopg')); print('PURE_MODELS_OK')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_public_deployment_example_loads_with_isolated_secret_files(tmp_path):
    from app.infrastructure.config import Settings

    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    for name in ("app_password", "web_session_secret"):
        file = secret_dir / name
        file.write_text("isolated-test-value-with-at-least-32-bytes")
        file.chmod(0o600)
    file = tmp_path / "service.json"
    file.write_bytes((ROOT / "deploy/config/service.example.json").read_bytes())
    settings = Settings.load(str(file))
    assert settings.database_user == "office_app" and settings.database_port == 5432
    assert settings.database_password_file == secret_dir / "app_password"
