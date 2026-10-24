import asyncio
import gzip
import json

import httpx
import pytest
from fastapi import FastAPI
from office_contracts import (
    ConfigurationError,
    ErrorInfo,
    Meta,
    ProtocolError,
    Success,
    decode,
    make_registry,
    prepare_success,
)
from office_contracts.integrations.fastapi import install_fastapi
from office_contracts.integrations.httpx import decode_response, request_json
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse


class Aliased(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", populate_by_name=True)
    label: str = Field(alias="displayName")


def test_aliases_and_semantic_roundtrip():
    value = Aliased(label="demo")
    prepared = prepare_success(value, Aliased, Meta(trace_id="t"))
    assert json.loads(prepared.body)["data"] == {"displayName": "demo"}
    assert decode(prepared.body, Success[Aliased]).data.label == "demo"


def test_nested_error_requires_registered_severity():
    with pytest.raises(ConfigurationError):
        prepare_success(ErrorInfo(code=999999, message="failed"), ErrorInfo, Meta(trace_id="t"))
    registry = make_registry("test")
    prepare_success(
        ErrorInfo(code=500001, message="failed"), ErrorInfo, Meta(trace_id="t"), registry=registry
    )


@pytest.mark.parametrize(
    "change",
    [
        {"code": False},
        {"code": 0.0},
        {"code": "0"},
        {"meta": {}},
        {"data": "bad"},
        {"message": None},
    ],
)
def test_client_rejects_core_type_conflict(change):
    value = {
        "code": 0,
        "message": "ok",
        "data": 1,
        "meta": {"trace_id": "t", "contract_version": "1"},
    }
    value.update(change)
    r = httpx.Response(200, json=value, request=httpx.Request("GET", "http://probe"))
    with pytest.raises(ProtocolError):
        decode_response(r, int, registry=make_registry("test"))


def test_decompressed_stream_not_decoded_twice():
    body = prepare_success(12, int, Meta(trace_id="t")).body
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            content=gzip.compress(body),
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
    )
    with httpx.Client(transport=transport) as client:
        assert (
            request_json(client, "GET", "http://probe", int, registry=make_registry("test")).data
            == 12
        )


def test_started_stream_is_not_rewritten_and_cancel_propagates():
    app = FastAPI()

    @app.get("/api/v2/stream")
    async def streaming():
        async def chunks():
            yield b"first"
            raise RuntimeError("synthetic")

        return StreamingResponse(chunks())

    @app.get("/api/v2/cancel")
    async def cancelled():
        raise asyncio.CancelledError()

    install_fastapi(app, registry=make_registry("test"))

    async def run(path, error):
        events = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(event):
            events.append(event)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("probe", 80),
            "client": ("probe", 1),
            "root_path": "",
        }
        with pytest.raises(error):
            await app(scope, receive, send)
        return events

    streamed = asyncio.run(run("/api/v2/stream", RuntimeError))
    assert len([e for e in streamed if e["type"] == "http.response.start"]) == 1
    cancelled_events = asyncio.run(run("/api/v2/cancel", asyncio.CancelledError))
    assert not any(e["type"] == "http.response.start" for e in cancelled_events)
