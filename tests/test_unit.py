import time
from types import SimpleNamespace

import jwt
import pytest
from app.common.errors import DomainError
from app.infrastructure.config import Settings
from app.modules.access.authentication import TokenVerifier
from app.modules.data.schemas import SchemaInput, validate_values
from cryptography.hazmat.primitives.asymmetric import rsa
from office_client.auth import ClientError, trusted_url
from office_client.product import PRODUCT


@pytest.mark.parametrize("value", [1.2, "NaN", "Infinity", True, "1e9", "0.000000001"])
def test_decimal_rejects_ambiguous_values(value):
    with pytest.raises(DomainError):
        validate_values([{"id": "amount", "type": "decimal", "required": True}], {"amount": value})


def test_dynamic_fields_and_unknown_fields():
    fields = [{"id": "new_project_field", "type": "text", "required": True}]
    assert validate_values(fields, {"new_project_field": "新增字段"}) == {
        "new_project_field": "新增字段"
    }
    with pytest.raises(DomainError):
        validate_values(fields, {"unknown": "不能自动变成新字段"})


def test_schema_rejects_duplicate_ids():
    field = {"id": "amount", "label": "金额", "type": "decimal", "description": "定义"}
    with pytest.raises(ValueError):
        SchemaInput.model_validate({"expected_revision": 0, "fields": [field, field]})


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com?a=1",
        "file:///tmp/a",
    ],
)
def test_client_rejects_unsafe_service_url(url):
    with pytest.raises(ClientError):
        trusted_url(url)


@pytest.mark.parametrize(
    "change",
    [
        {"aud": "other-service"},
        {"iss": "https://other.invalid"},
        {"exp": int(time.time()) - 60},
        {"typ": "ID"},
        {"sid": None},
        {"azp": []},
        {"sid": ""},
        {"iat": True},
        {"auth_time": "invalid"},
        {"exp": 99999999999999999999},
        {"sub": None},
        {"scope": None},
        {"scope": ["office"]},
        {"scope": 123},
    ],
)
def test_verifier_rejects_signed_wrong_claims(tmp_path, change):
    settings = Settings(
        database_user="unused",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid/realms/office",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    verifier = TokenVerifier(settings)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier.jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
    )
    claims = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": "test",
        "typ": "Bearer",
        "sid": "test-session",
        "azp": PRODUCT["client_id"],
        "scope": "openid office",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        **change,
    }
    token = jwt.encode(claims, key, algorithm="RS256")
    with pytest.raises(DomainError) as caught:
        verifier.verify(token)
    assert caught.value.status == 401


def test_cli_unconfirmed_write_fails_before_network(tmp_path):
    import json
    import subprocess
    import sys

    source = tmp_path / "record.json"
    source.write_text(json.dumps({"project_id": "test", "values": {"value": "sample"}}))
    preview = subprocess.run(
        [
            sys.executable,
            "-m",
            "office_client.cli",
            "--service",
            "https://office.example",
            "preview",
            "--file",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # 直接调用安装入口，避免依赖工作树导入位置。
    from unittest.mock import patch

    from office_client.cli import main

    with (
        patch(
            "sys.argv",
            [
                "office",
                "--service",
                "https://office.example",
                "submit",
                "--file",
                str(source),
                "--confirm-digest",
                "wrong",
            ],
        ),
        pytest.raises(SystemExit) as caught,
    ):
        main()
    assert caught.value.code == 1
    assert preview.returncode == 0
    assert len(json.loads(preview.stdout)["digest"]) == 64


def test_server_rejects_owner_runtime(tmp_path):
    from app.main import create_app

    settings = Settings(
        database_user="office_owner",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    with pytest.raises(RuntimeError, match="最小权限"):
        create_app(settings)


def test_oauth_rejects_wrong_state_before_code_exchange():
    import socket
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from urllib.parse import parse_qs, urlparse

    import httpx
    from office_client.auth import OAuth

    class EmptyStore:
        saved = False

        def save(self, value):
            self.saved = True

    store, urls = EmptyStore(), Queue()
    # 不占用员工实际登录常用的8765端口，测试只连接本轮临时监听器。
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    callback = f"http://127.0.0.1:{port}/callback"
    oauth = OAuth("http://127.0.0.1:8800", store, browser_open=urls.put)
    oauth._config = {
        "issuer": "http://127.0.0.1:8081/realms/office",
        "client_id": PRODUCT["client_id"],
        "redirect_uri": callback,
        "scope": "openid office",
    }
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(oauth.login, 5)
            state = parse_qs(urlparse(urls.get(timeout=2)).query)["state"][0]
            wrong = httpx.get(callback, params={"code": "injected", "state": "wrong"})
            assert wrong.status_code == 400
            httpx.get(callback, params={"error": "access_denied", "state": state})
            with pytest.raises(ClientError, match="未完成授权"):
                result.result(timeout=2)
        assert not store.saved
    finally:
        oauth.close()
