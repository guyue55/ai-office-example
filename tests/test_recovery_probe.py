"""恢复探针不能把其他认证表单的隐藏动作混入密码登录。"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recovery_oidc import RestoredIdentityTransport


def test_recovery_transport_refuses_source_and_external_addresses():
    transport = RestoredIdentityTransport("http://127.0.0.1:8081", 65500)
    try:
        for url in ("https://example.com/", "http://127.0.0.1:8800/", "http://localhost:8081/"):
            with pytest.raises(RuntimeError):
                transport.handle_request(httpx.Request("GET", url))
    finally:
        transport.close()


@pytest.mark.parametrize(
    "base,port",
    [
        ("https://example.com", 65500),
        ("http://127.0.0.1:8081", 8081),
        ("http://127.0.0.1:8081", 8800),
    ],
)
def test_recovery_route_configuration_cannot_target_primary(base, port):
    with pytest.raises(ValueError):
        RestoredIdentityTransport(base, port)
