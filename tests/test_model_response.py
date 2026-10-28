"""传输层原始／解压预算，覆盖错误体、压缩炸弹和取消释放。"""

import asyncio
import gzip
import json
import socket
import zlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from app.infrastructure.model_egress import ModelEgressError, model_transport
from app.infrastructure.model_response import bounded_response

httpx2 = pytest.importorskip("httpx2")


class Body(httpx2.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = 0
        self.read = 0

    async def __aiter__(self):
        for data in self.chunks:
            self.read += 1
            yield data

    async def aclose(self):
        self.closed += 1


def read(chunks, *, encoding=None, status=200, wire=64, decoded=64, headers=None):
    body = Body(chunks)
    values = dict(headers or {})
    if encoding:
        values["content-encoding"] = encoding
    response = bounded_response(
        httpx2.Response(status, headers=values, stream=body), wire_limit=wire, decoded_limit=decoded
    )
    return response, body


@pytest.mark.parametrize("encoding", [None, "gzip", "deflate"])
def test_bounded_success_and_exact_boundary(encoding):
    raw = b"x" * 64
    payload = gzip.compress(raw) if encoding == "gzip" else zlib.compress(raw) if encoding else raw
    response, body = read([payload[:2], payload[2:]], encoding=encoding)
    assert asyncio.run(response.aread()) == raw
    assert "content-encoding" not in response.headers
    assert body.closed == 1


@pytest.mark.parametrize("status", [200, 400, 401, 429, 500])
@pytest.mark.parametrize("encoding", [None, "gzip", "deflate"])
def test_oversized_success_and_error_body_close_early(status, encoding):
    raw = b"x" * 1048576
    payload = gzip.compress(raw) if encoding == "gzip" else zlib.compress(raw) if encoding else raw
    response, body = read([payload, b"unread"], encoding=encoding, status=status, wire=2097152)
    with pytest.raises(ModelEgressError) as error:
        asyncio.run(response.aread())
    assert error.value.category == "response_limit"
    assert body.closed == 1 and body.read == 1


def test_raw_budget_is_independent_and_header_not_trusted():
    response, body = read(
        [b"x" * 33, b"x" * 33], wire=64, decoded=128, headers={"content-length": "1"}
    )
    with pytest.raises(ModelEgressError):
        asyncio.run(response.aread())
    assert body.closed == 1
    response, body = read([b"never-read"], headers={"content-length": "1000"})
    with pytest.raises(ModelEgressError):
        asyncio.run(response.aread())
    assert body.read == 0 and body.closed == 1


@pytest.mark.parametrize(
    "encoding,payload",
    [
        ("gzip", b"broken"),
        ("gzip", gzip.compress(b"ok")[:-2]),
        ("gzip", gzip.compress(b"ok") + gzip.compress(b"extra")),
        ("br", b"unsupported"),
        ("gzip, gzip", b"unsupported"),
    ],
)
def test_invalid_compression_is_rejected_and_closed(encoding, payload):
    response, body = read([payload], encoding=encoding, wire=256)
    with pytest.raises(ModelEgressError) as error:
        asyncio.run(response.aread())
    assert error.value.category == "invalid_response" and body.closed == 1


def test_response_read_cancellation_closes_original_stream():
    async def run():
        entered = asyncio.Event()

        class Waiting(Body):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b""

        body = Waiting([])
        response = bounded_response(
            httpx2.Response(200, stream=body), wire_limit=64, decoded_limit=64
        )
        task = asyncio.create_task(response.aread())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed == 1

    asyncio.run(run())


def test_sdk_probe_rejects_large_response_despite_small_reported_token_usage():
    pytest.importorskip("office_agent")
    from app.application.model_runtime import test_runtime
    from office_agent.config import AgentConfig
    from office_agent.providers import configured_model

    async def run():
        settings = SimpleNamespace(
            agent_model_url_policy="public",
            agent_model_urls=[],
            agent_model_response_wire_bytes=4194304,
            agent_model_response_decoded_bytes=4194304,
        )

        async def resolve(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

        payload = {
            "id": "synthetic",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "x" * (2 * 1024 * 1024)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        raw = json.dumps(payload).encode()
        body = Body([raw[i : i + 16384] for i in range(0, len(raw), 16384)])

        def serve(request):
            return httpx2.Response(200, headers={"content-type": "application/json"}, stream=body)

        config = AgentConfig(
            service_url="https://office.invalid",
            model_url="https://model.invalid/v1",
            model="fixture",
            approved_model_data=True,
        )

        @asynccontextmanager
        async def context(bound):
            transport = model_transport(
                settings,
                bound.model_url,
                resolver=resolve,
                inner=httpx2.MockTransport(serve),
                max_response_bytes=65536,
            )
            async with configured_model(bound, key="synthetic", transport=transport) as model:
                yield model

        runner = SimpleNamespace(
            application=SimpleNamespace(settings=SimpleNamespace(operation_timeout=15)),
            model_context=context,
        )
        result = await test_runtime(config, runner)
        assert result["passed"] is False and result["category"] == "response_limit"
        assert body.closed == 1 and body.read == 5

    asyncio.run(run())


def test_managed_prepare_selects_probe_budget_without_changing_active_policy(tmp_path, monkeypatch):
    pytest.importorskip("office_agent")
    from app.application.model_runtime import prepare_runtime
    from app.infrastructure.model_secrets import encrypt
    from app.modules.agent.model_configuration import ModelDraft
    from cryptography.fernet import Fernet

    key = tmp_path / "synthetic-root"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    observed = []

    def transport(settings, url, *, max_response_bytes):
        observed.append(max_response_bytes)
        return object()

    @asynccontextmanager
    async def provider(config, *, key, transport):
        yield "synthetic"

    monkeypatch.setattr("app.application.model_runtime.model_transport", transport)
    monkeypatch.setattr("office_agent.providers.configured_model", provider)

    async def run():
        settings = SimpleNamespace(
            agent_model_url_policy="public",
            agent_model_urls=[],
            agent_secret_key_file=key,
            public_url="https://office.invalid",
            allow_lan_http_demo=False,
            agent_max_running=8,
        )
        application = SimpleNamespace(settings=settings, sessions=None)
        revision = SimpleNamespace(
            config=ModelDraft(
                model_url="https://model.invalid/v1", model="fixture", approved_model_data=True
            ).model_dump(),
            secret_ciphertext=encrypt(key, "synthetic"),
        )
        for synthetic in [True, False]:
            config, runner = await prepare_runtime(application, revision, synthetic=synthetic)
            async with runner.model_context(config) as model:
                assert model == "synthetic"
        assert observed == [65536, None]

    asyncio.run(run())


@pytest.mark.parametrize(
    "length,category",
    [("-1", "invalid_response"), ("bad", "invalid_response"), ("9" * 5000, "response_limit")],
)
def test_invalid_or_extreme_length_does_not_read_body(length, category):
    response, body = read([b"never-read"], headers={"content-length": length})
    with pytest.raises(ModelEgressError) as error:
        asyncio.run(response.aread())
    assert error.value.category == category and body.read == 0 and body.closed == 1
