"""只使用合成解析、HTTP替身和本地TLS，不请求真实模型。"""

import asyncio
import socket
from datetime import UTC
from types import SimpleNamespace

import pytest
from app.application.model_test_errors import failure
from app.common.errors import DomainError
from app.infrastructure.model_egress import ModelEgressError, check_endpoint, model_transport

httpx2 = pytest.importorskip("httpx2")
pytest.importorskip("office_agent")


def settings(policy="public", urls=()):
    return SimpleNamespace(agent_model_url_policy=policy, agent_model_urls=list(urls))


def test_static_policy_and_legacy_config(tmp_path):
    from app.infrastructure.config import Settings

    base = {
        "database_user": "fixture",
        "database_password_file": tmp_path / "unused",
        "issuer": "https://identity.invalid",
        "audience": "https://office.invalid",
        "public_url": "https://office.invalid",
    }
    assert Settings(**base).agent_model_url_policy == "public"
    assert (
        Settings(**base, agent_model_urls=["https://model.invalid"]).agent_model_url_policy
        == "allowlist"
    )
    assert (
        Settings(
            **base, agent_model_urls=["https://model.invalid"], agent_model_url_policy="public"
        ).agent_model_url_policy
        == "public"
    )
    check_endpoint(settings(), "https://model.invalid/v1")
    for url in [
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://192.168.1.2",
        "http://localhost",
    ]:
        with pytest.raises(DomainError):
            check_endpoint(settings(), url)
    with pytest.raises(DomainError):
        check_endpoint(settings("allowlist"), "https://model.invalid")
    with pytest.raises(DomainError):
        check_endpoint(settings(urls=["https://169.254.169.254"]), "https://169.254.169.254")


def test_transport_pins_dns_checks_every_candidate_and_blocks_redirect():
    async def scenario():
        seen, calls = [], []
        ips = ["8.8.8.8"]

        async def resolve(host, port, **kwargs):
            calls.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

        async def serve(request):
            seen.append(request)
            return httpx2.Response(200, json={"ok": True})

        transport = model_transport(
            settings(),
            "https://model.invalid/v1",
            resolver=resolve,
            inner=httpx2.MockTransport(serve),
        )
        async with httpx2.AsyncClient(transport=transport) as client:
            assert (
                await client.post("https://model.invalid/v1/responses", json={})
            ).status_code == 200
            assert seen[0].url.host == "8.8.8.8"
            assert seen[0].headers["host"] == "model.invalid"
            assert seen[0].extensions["sni_hostname"] == "model.invalid"
            ips[:] = ["8.8.8.8", "127.0.0.1"]
            with pytest.raises(ModelEgressError):
                await client.get("https://model.invalid/v1/responses")
            assert len(seen) == 1 and len(calls) == 2
            with pytest.raises(ModelEgressError):
                await client.get("https://other.invalid/v1")
        transport = model_transport(
            settings(),
            "https://model.invalid",
            resolver=resolve,
            inner=httpx2.MockTransport(
                lambda req: httpx2.Response(302, headers={"location": "https://other.invalid"})
            ),
        )
        ips[:] = ["8.8.8.8"]
        async with httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(ModelEgressError):
                await client.get("https://model.invalid")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,category",
    [
        (401, "authentication"),
        (403, "permission"),
        (404, "endpoint_or_model"),
        (429, "rate_limit"),
        (503, "provider"),
    ],
)
def test_error_classification_never_exposes_provider_body(status, category):
    error = type("ProviderError", (Exception,), {"status_code": status})("DO-NOT-EXPOSE")
    result = failure(error)
    assert result["category"] == category and "DO-NOT-EXPOSE" not in str(result)


def test_local_tls_pinning_preserves_certificate_identity(tmp_path):
    import ssl
    import threading
    from datetime import datetime, timedelta
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "model.invalid")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("model.invalid")]), False)
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    keyfile.chmod(0o600)
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            observed.append(self.headers["Host"])
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    context.set_servername_callback(lambda sock, host, ctx: observed.append(host))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        port = server.server_port
        url = f"https://model.invalid:{port}"

        async def resolver(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        trust = ssl.create_default_context(cafile=str(certfile))
        transport = model_transport(
            settings(urls=[url]),
            url,
            resolver=resolver,
            inner=httpx2.AsyncHTTPTransport(verify=trust),
        )
        async with httpx2.AsyncClient(transport=transport) as client:
            assert (await client.get(url + "/v1")).text == "OK"
        assert observed == ["model.invalid", f"model.invalid:{port}"]
        transport = model_transport(settings(urls=[url]), url, resolver=resolver)
        async with httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx2.ConnectError):
                await client.get(url)

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
def test_managed_three_protocols_over_real_loopback_http(api):
    import json
    import threading
    from contextlib import asynccontextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.application.model_runtime import test_runtime
    from office_agent.config import AgentConfig
    from office_agent.providers import configured_model

    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append((self.path, body))
            assert self.headers["Authorization"] == "Bearer synthetic-key"
            assert not body.get("tools")
            if api == "openai-completions":
                data = {
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "fixture",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "OK"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
                }
            elif api == "openai-responses":
                data = {
                    "id": "resp_fixture",
                    "object": "response",
                    "created_at": 1,
                    "model": "fixture",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "id": "msg_fixture",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "OK", "annotations": []}],
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
                }
            else:
                data = {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": "fixture",
                    "content": [{"type": "text", "text": "OK"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                }
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        url = f"http://127.0.0.1:{server.server_port}" + (
            "" if api == "anthropic-messages" else "/v1"
        )
        config = AgentConfig(
            service_url="https://office.invalid",
            model_url=url,
            api=api,
            model="fixture",
            approved_model_data=True,
        )

        @asynccontextmanager
        async def context(bound):
            async with configured_model(
                bound, key="synthetic-key", transport=model_transport(settings(urls=[url]), url)
            ) as model:
                yield model

        runner = SimpleNamespace(
            application=SimpleNamespace(settings=SimpleNamespace(operation_timeout=15)),
            model_context=context,
        )
        result = await test_runtime(config, runner)
        assert result["passed"] and result["category"] == "ok", result
        assert len(observed) == 1
        assert (
            observed[0][0]
            == {
                "openai-completions": "/v1/chat/completions",
                "openai-responses": "/v1/responses",
                "anthropic-messages": "/v1/messages",
            }[api]
        )

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
