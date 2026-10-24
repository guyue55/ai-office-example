"""使用官方MCP模型，结构化结果与文本回退同源。"""

import json

from mcp.types import CallToolResult, TextContent
from pydantic import TypeAdapter

from ..codec import encode, load_json, prepare_success, success_model
from ..models import Failure, Meta


def output_schema(data_model):
    return {"type": "object", **TypeAdapter(success_model(data_model) | Failure).json_schema()}


def tool_success[T](value: T, data_model: type[T], meta: Meta, *, registry=None) -> CallToolResult:
    raw = prepare_success(value, data_model, meta, registry=registry).body
    return CallToolResult(
        content=[TextContent(type="text", text=raw.decode())],
        structured_content=load_json(raw),
        is_error=False,
    )


def tool_failure(code: int, status: int, meta: Meta, *, registry) -> CallToolResult:
    failure = registry.failure(code, status, meta)
    raw = encode(failure, Failure)
    return CallToolResult(
        content=[TextContent(type="text", text=raw.decode())],
        structured_content=json.loads(raw),
        is_error=True,
    )
