"""仅静态检查公共泛型，不发送请求。"""

from typing import assert_type

import httpx
from office_contracts import Meta, PreparedSuccess, Success, decode, make_registry, prepare_success
from office_contracts.integrations.httpx import (
    NativeResult,
    arequest_json,
    decode_response,
    request_json,
)
from pydantic import BaseModel


class Item(BaseModel):
    quantity: int


def consumer(response: httpx.Response, client: httpx.Client):
    registry = make_registry("inventory")
    assert_type(prepare_success(Item(quantity=1), Item, Meta(trace_id="t")), PreparedSuccess[Item])
    assert_type(decode(b"", Success[Item]), Success[Item])
    assert_type(decode_response(response, Item, registry=registry), Success[Item] | NativeResult)
    assert_type(
        request_json(client, "GET", "http://example.invalid", Item, registry=registry),
        Success[Item] | NativeResult,
    )


async def async_consumer(client: httpx.AsyncClient):
    assert_type(
        await arequest_json(
            client, "GET", "http://example.invalid", Item, registry=make_registry("inventory")
        ),
        Success[Item] | NativeResult,
    )
