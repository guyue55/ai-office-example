import asyncio
import json
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from office_contracts import ContractError, Meta, ProtocolError, RemoteError, Success, make_registry
from office_contracts.codec import Budget
from office_contracts.integrations.fastapi import ContractRouter, install_fastapi
from office_contracts.integrations.httpx import (
    NativeResult,
    arequest_json,
    decode_response,
    request_json,
)
from office_contracts.integrations.mcp import output_schema, tool_failure, tool_success
from pydantic import BaseModel, ConfigDict, TypeAdapter


class Item(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    quantity: int


@pytest.fixture
def fixture():
    registry = make_registry("test")
    app = FastAPI()
    api = ContractRouter(registry=registry, prefix="/api/v2")
    calls = []

    def dependency(response: Response):
        calls.append(1)
        response.set_cookie("one", "synthetic")
        response.set_cookie("two", "synthetic")

    @api.get("/item", data_model=Item)
    async def item(dep: Annotated[None, Depends(dependency)]):
        return Item(quantity=12)

    @api.post("/item", data_model=Item, status_code=201)
    async def create(body: Item):
        return body

    @api.get("/fail", data_model=Item, errors=[409001])
    async def fail():
        raise ContractError(409001)

    @api.get("/raw", data_model=Item)
    def raw():
        return Response(b"unsafe")

    @app.head("/api/v2/denied")
    def denied():
        raise HTTPException(401)

    @app.get("/api/v2/native")
    def native():
        return Response(status_code=205)

    @app.get("/api/v1/old")
    def old():
        return {"legacy": True}

    app.include_router(api)
    install_fastapi(app, registry=registry)
    return app, registry, calls


def test_http_signature_dependencies_headers_schema(fixture):
    app, registry, calls = fixture
    with TestClient(app) as client:
        response = client.get("/api/v2/item")
        assert response.status_code == 200
        assert response.json()["data"] == {"quantity": 12}
        assert len(calls) == 1 and len(response.headers.get_list("set-cookie")) == 2
        assert decode_response(response, Item, registry=registry).data.quantity == 12
        assert client.get("/api/v1/old").json() == {"legacy": True}
        spec = client.get("/openapi.json").json()
        operation = spec["paths"]["/api/v2/item"]["post"]
        assert "requestBody" in operation and "201" in operation["responses"]
        assert operation["responses"]["422"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/Failure")


@pytest.mark.parametrize(
    "path,status,code", [("missing", 404, 404001), ("fail", 409, 409001), ("raw", 500, 500002)]
)
def test_error_paths(fixture, path, status, code):
    app, _, _ = fixture
    with TestClient(app) as client:
        response = client.get("/api/v2/" + path)
        assert response.status_code == status
        assert response.json()["code"] == code
        assert response.headers["x-trace-id"] == response.json()["meta"]["trace_id"]


def test_input_native_and_no_sensitive_reflection(fixture):
    app, registry, _ = fixture
    with TestClient(app) as client:
        bad = client.post("/api/v2/item", json={"quantity": "private-marker"})
        assert bad.status_code == 422 and "private-marker" not in bad.text
        duplicate = client.post(
            "/api/v2/item",
            content=b'{"quantity":1,"quantity":2}',
            headers={"Content-Type": "application/json"},
        )
        assert duplicate.status_code == 400
        assert client.head("/api/v2/denied").content == b""
        reset = client.get("/api/v2/native")
        assert reset.content == b"" and isinstance(
            decode_response(reset, Item, registry=registry, native=True), NativeResult
        )
        assert client.put("/api/v2/item").headers["allow"]


def test_sync_and_async_client(fixture):
    app, registry, _ = fixture
    with TestClient(app) as transport_client:
        body = transport_client.get("/api/v2/item").content
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=body, headers={"Content-Type": "application/json"}
            )
        )
    ) as client:
        assert (
            request_json(client, "GET", "http://probe/item", Item, registry=registry).data.quantity
            == 12
        )
        with pytest.raises(ProtocolError):
            request_json(
                client,
                "GET",
                "http://probe/item",
                Item,
                registry=registry,
                budget=Budget(max_bytes=10),
            )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://probe"
        ) as client:
            assert (
                await arequest_json(client, "GET", "/api/v2/item", Item, registry=registry)
            ).data.quantity == 12

    asyncio.run(run())


def test_client_unknown_code_and_nested_extensions():
    registry = make_registry("test")
    body = {
        "code": 900001,
        "message": "private-marker",
        "data": None,
        "meta": {"trace_id": "t", "contract_version": "1", "future": True},
    }
    response = httpx.Response(409, json=body, request=httpx.Request("GET", "http://probe"))
    with pytest.raises(RemoteError) as error:
        decode_response(response, Item, registry=registry)
    assert error.value.code == 900001 and "private-marker" not in error.value.failure.message
    body["code"] = 0
    with pytest.raises(ProtocolError):
        decode_response(
            httpx.Response(409, json=body, request=response.request), Item, registry=registry
        )


def test_mcp_real_sdk_schema_and_text():
    registry = make_registry("test")
    result = tool_success(Item(quantity=12), Item, Meta(trace_id="t"))
    assert json.loads(result.content[0].text) == result.structured_content
    assert (
        TypeAdapter(Success[Item])
        .validate_json(json.dumps(result.structured_content))
        .data.quantity
        == 12
    )
    assert output_schema(Item)["anyOf"]
    failure = tool_failure(409001, 409, Meta(trace_id="t"), registry=registry)
    assert failure.is_error is True and failure.structured_content["code"] == 409001


def test_mcp_output_matches_actual_declared_json_schema():
    from jsonschema import validate

    registry = make_registry("test")
    schema = output_schema(Item)
    for result in (
        tool_success(Item(quantity=1), Item, Meta(trace_id="t")),
        tool_failure(409001, 409, Meta(trace_id="t"), registry=registry),
    ):
        validate(result.structured_content, schema)


def test_generic_http_ranges_do_not_expand_two_hundred_statuses():
    registry = make_registry("test")
    app = FastAPI()
    api = ContractRouter(registry=registry, prefix="/api/v2")

    @api.get("/item", data_model=Item, errors=[400099, 500099])
    def item():
        return Item(quantity=1)

    app.include_router(api)
    responses = app.openapi()["paths"]["/api/v2/item"]["get"]["responses"]
    assert {"4XX", "5XX"} <= responses.keys()
    assert len(responses) < 20


def test_contract_error_401_also_keeps_auth_challenge():
    app = FastAPI()
    registry = make_registry("test")
    api = ContractRouter(registry=registry, prefix="/api/v2")

    @api.get("/protected", data_model=Item, errors=[401001])
    def protected():
        raise ContractError(401001)

    app.include_router(api)
    install_fastapi(app, registry=registry)
    with TestClient(app) as client:
        response = client.get("/api/v2/protected")
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"


def test_http_429_falls_back_to_generic_client_error():
    app = FastAPI()
    registry = make_registry("test")
    api = ContractRouter(registry=registry, prefix="/api/v2")

    @api.get("/limited", data_model=Item)
    def limited():
        raise HTTPException(429, headers={"Retry-After": "30"})

    app.include_router(api)
    install_fastapi(app, registry=registry)
    with TestClient(app) as client:
        response = client.get("/api/v2/limited")
        assert response.status_code == 429
        assert response.json()["code"] == 400099
        assert response.headers["retry-after"] == "30"


def test_validation_error_details_locate_fields():
    app = FastAPI()
    registry = make_registry("test")
    api = ContractRouter(registry=registry, prefix="/api/v2")

    @api.post("/query", data_model=Item)
    def query(body: Item):
        return body

    app.include_router(api)
    install_fastapi(app, registry=registry)
    with TestClient(app) as client:
        response = client.post("/api/v2/query", json={"quantity": "bad", "extra": 1})
        payload = response.json()
        assert response.status_code == 422 and payload["code"] == 422001
        paths = {tuple(i["path"]) for i in payload["details"]}
        assert ("body", "quantity") in paths and ("body", "extra") in paths
        assert all(set(i.keys()) <= {"path", "reason", "message"} for i in payload["details"])
