"""有界请求体缓冲与读取期限；不缓冲响应流。"""

import asyncio

from app.common.errors import DomainError
from app.entrypoints.errors import error_response


class BodyLimitMiddleware:
    def __init__(self, app, limit=1024 * 1024, timeout=10):
        self.app, self.limit, self.timeout = app, limit, timeout

    async def __call__(self, scope, receive, send):
        # v2 契约边界在外层先行拦截 413/408/415/坏JSON；本中间件仅兜底非契约路径。
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = scope.get("state", {}).get("trace_id")
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length", b"")
        if length.isdigit() and int(length) > self.limit:
            await error_response(
                DomainError(413, "request_too_large", "请求超过大小限制"), trace_id
            )(scope, receive, send)
            return
        body = bytearray()
        try:
            async with asyncio.timeout(self.timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.limit:
                        await error_response(
                            DomainError(413, "request_too_large", "请求超过大小限制"), trace_id
                        )(scope, receive, send)
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await error_response(DomainError(408, "request_timeout", "请求体读取超时"), trace_id)(
                scope, receive, send
            )
            return
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
