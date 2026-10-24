"""纯ASGI边界：有界输入、追踪与开始发送前的安全错误。"""

import asyncio
from contextvars import ContextVar
from uuid import uuid4

from starlette.responses import Response

from ..codec import Budget, encode, load_json
from ..errors import ContractError, ProtocolError
from ..models import Failure, Meta

REQUEST_BUDGET = Budget(max_bytes=1024 * 1024)

trace_context: ContextVar[str | None] = ContextVar("contract_trace", default=None)


class Renderer:
    def __init__(self, registry):
        self.registry = registry

    def response(self, code, status, *, headers=None, meta=None, details=(), truncated=False):
        meta = meta or Meta(
            trace_id=trace_context.get() or str(uuid4()), details_truncated=truncated
        )
        headers = dict(headers or {})
        if status == 401 and not any(k.lower() == "www-authenticate" for k in headers):
            headers["WWW-Authenticate"] = "Bearer"
        failure = self.registry.failure(code, status, meta, details=details)
        return Response(
            encode(failure, Failure),
            status_code=status,
            media_type="application/json",
            headers=headers,
        )


class ContractBoundary:
    def __init__(
        self,
        app,
        *,
        registry,
        prefixes,
        request_budget=REQUEST_BUDGET,
        body_timeout=10,
    ):
        self.app, self.prefixes = app, tuple(p.rstrip("/") for p in prefixes)
        self.renderer, self.request_budget, self.body_timeout = (
            Renderer(registry),
            request_budget,
            body_timeout,
        )

    def matches(self, path):
        return any(path == p or path.startswith(p + "/") for p in self.prefixes)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.matches(scope["path"]):
            await self.app(scope, receive, send)
            return
        trace_id = str(uuid4())
        scope.setdefault("state", {})["trace_id"] = trace_id
        token = trace_context.set(trace_id)
        started = False
        no_body = scope["method"] == "HEAD"

        async def traced_send(message):
            nonlocal started, no_body
            if message["type"] == "http.response.start":
                started = True
                no_body = no_body or message["status"] in {204, 205, 304}
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-trace-id"
                ]
                if message["status"] in {204, 205}:
                    headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
                headers.append((b"x-trace-id", trace_id.encode()))
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            if no_body and message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        try:
            body = bytearray()
            async with asyncio.timeout(self.body_timeout):
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    chunk = event.get("body", b"")
                    if len(body) + len(chunk) > self.request_budget.max_bytes:
                        await self.renderer.response(413001, 413)(scope, receive, traced_send)
                        return
                    body.extend(chunk)
                    if not event.get("more_body", False):
                        break
            if body:
                media = (
                    dict(scope.get("headers", []))
                    .get(b"content-type", b"")
                    .split(b";", 1)[0]
                    .strip()
                    .lower()
                )
                if media != b"application/json":
                    await self.renderer.response(415001, 415)(scope, receive, traced_send)
                    return
                try:
                    load_json(bytes(body), self.request_budget)
                except ProtocolError:
                    await self.renderer.response(400002, 400)(scope, receive, traced_send)
                    return
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay, traced_send)
        except Exception as error:
            if started:
                raise
            code, status = (408001, 408) if isinstance(error, TimeoutError) else (500001, 500)
            if isinstance(error, ProtocolError):
                code = 500002
            if isinstance(error, ContractError):
                spec = self.renderer.registry.by_code(error.code)
                if spec and spec.severity == "error":
                    code, status = spec.code, min(spec.statuses)
            await self.renderer.response(code, status)(scope, receive, traced_send)
        finally:
            trace_context.reset(token)
