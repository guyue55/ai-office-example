"""复用调用方连接池；无身份管理和自动重试。"""

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import cast

import httpx

from ..codec import DEFAULT_BUDGET, decode, load_json
from ..errors import ProtocolError, RemoteError, TransportFailure
from ..models import Success
from ..policy import transport_recovery
from ..registry import CodeRegistry
from .readers import ReadFailure, ReadSuccess
from .sync_deadline import within_deadline


@dataclass(frozen=True, slots=True)
class NativeResult:
    status: int
    headers: tuple[tuple[str, str], ...]


def decode_response[T](
    response: httpx.Response,
    data_model: type[T],
    *,
    registry: CodeRegistry,
    budget=DEFAULT_BUDGET,
    native=False,
) -> Success[T] | NativeResult:
    try:
        return _decode_response(
            response, data_model, registry=registry, budget=budget, native=native
        )
    except ProtocolError as error:
        error.status = response.status_code
        trace = response.headers.get("x-trace-id", "")
        error.trace_id = trace if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", trace) else None
        raise


def _decode_response[T](response: httpx.Response, data_model: type[T], *, registry, budget, native):
    if response.request.method == "HEAD" or response.status_code in {204, 205, 304}:
        if response.request.method != "HEAD" and not native:
            raise ProtocolError("此操作未声明无正文响应")
        return NativeResult(response.status_code, tuple(response.headers.multi_items()))
    if native:
        raise ProtocolError("原生下载请使用调用方的流式接口")
    if (
        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise ProtocolError("未获得契约JSON响应")
    value = load_json(response.content, budget)
    if not isinstance(value, dict) or not {"code", "message", "data", "meta"} <= value.keys():
        raise ProtocolError()
    code = value["code"]
    if type(code) is not int or not 0 <= code <= 2147483647:
        raise ProtocolError()
    if not isinstance(value["meta"], dict) or value["meta"].get("contract_version") != "1":
        raise ProtocolError("不支持的线协议版本")
    trace = value["meta"].get("trace_id")
    if response.headers.get("x-trace-id", trace) != trace:
        raise ProtocolError("追踪信息冲突")
    if code == 0:
        if not 200 <= response.status_code < 300:
            raise ProtocolError("HTTP状态与业务码冲突")
        # 动态具体化T；cast保留具体泛型，不使用Any绕过业务校验。
        model = cast(type[ReadSuccess[T]], ReadSuccess.__class_getitem__(data_model))
        return decode(response.content, model, budget)
    if not 400 <= response.status_code < 600:
        raise ProtocolError("HTTP状态与业务码冲突")
    spec = registry.by_code(code)
    if spec and (spec.severity != "error" or response.status_code not in spec.statuses):
        raise ProtocolError("业务码与错误状态冲突")
    # 先校验原始形状，再用受控文案创建异常，不能覆盖坏类型掩盖协议错误。
    decode(response.content, ReadFailure, budget)
    value["message"] = spec.message if spec else "远端返回未识别错误，请核对服务版本"
    for issue in value.get("details", []):
        issue.update(path=["field"], reason="invalid", message="请核对该字段")
    if not spec:
        value["meta"].pop("recovery", None)
    failure = decode(
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), ReadFailure, budget
    )

    raise RemoteError(failure, response.status_code)


def _transport(error, writing):
    definitely = isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
    return TransportFailure(
        type(error).__name__,
        transport_recovery(writing=writing, definitely_not_sent=definitely).write_outcome,
    )


def request_json[T](
    client: httpx.Client,
    method: str,
    url: str,
    data_model: type[T],
    *,
    registry: CodeRegistry,
    budget=DEFAULT_BUDGET,
    total_timeout=30,
    native=False,
    **kwargs,
) -> Success[T] | NativeResult:
    started = time.monotonic()
    writing = method.upper() not in {"GET", "HEAD"}

    def perform(cancelled):
        try:
            if cancelled.is_set():
                raise TransportFailure("cancelled", "not_applied" if writing else "not_applicable")
            # 网络各阶段也有上界；调用方有更短timeout时不扩大。
            options = dict(kwargs)
            requested = options.pop("timeout", client.timeout)
            configured = httpx.Timeout(requested)
            options["timeout"] = httpx.Timeout(
                **{
                    name: min(value, total_timeout) if value is not None else total_timeout
                    for name, value in configured.as_dict().items()
                }
            )
            with client.stream(method, url, follow_redirects=False, **options) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    if cancelled.is_set() or time.monotonic() - started > total_timeout:
                        raise TransportFailure(
                            "total_timeout", "unknown" if writing else "not_applicable"
                        )
                    if len(content) + len(chunk) > budget.max_bytes:
                        raise ProtocolError("JSON超过结果预算")
                    content.extend(chunk)
                loaded = httpx.Response(
                    response.status_code,
                    headers=[
                        (k, v)
                        for k, v in response.headers.multi_items()
                        if k.lower()
                        not in {"content-encoding", "content-length", "transfer-encoding"}
                    ],
                    content=bytes(content),
                    request=response.request,
                )
                return decode_response(
                    loaded, data_model, registry=registry, budget=budget, native=native
                )
        except httpx.TransportError as error:
            raise _transport(error, writing) from None

    return within_deadline(perform, total_timeout, writing=writing)


async def arequest_json[T](
    client: httpx.AsyncClient,
    method: str,
    url: str,
    data_model: type[T],
    *,
    registry: CodeRegistry,
    budget=DEFAULT_BUDGET,
    total_timeout=30,
    native=False,
    **kwargs,
) -> Success[T] | NativeResult:
    try:
        async with asyncio.timeout(total_timeout):
            async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > budget.max_bytes:
                        raise ProtocolError("JSON超过结果预算")
                    content.extend(chunk)
                loaded = httpx.Response(
                    response.status_code,
                    headers=[
                        (k, v)
                        for k, v in response.headers.multi_items()
                        if k.lower()
                        not in {"content-encoding", "content-length", "transfer-encoding"}
                    ],
                    content=bytes(content),
                    request=response.request,
                )
                return decode_response(
                    loaded, data_model, registry=registry, budget=budget, native=native
                )
    except httpx.TransportError as error:
        raise _transport(error, method.upper() not in {"GET", "HEAD"}) from None
    except TimeoutError:
        raise TransportFailure(
            "total_timeout",
            "unknown" if method.upper() not in {"GET", "HEAD"} else "not_applicable",
        ) from None
