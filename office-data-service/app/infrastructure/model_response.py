"""模型HTTP响应的独立传输预算；不能依赖供应商遵守Token参数。"""

import zlib

from app.infrastructure.model_errors import ModelEgressError


def bounded_response(response, *, wire_limit, decoded_limit):
    import httpx2

    class Stream(httpx2.AsyncByteStream):
        def __init__(self):
            self.closed = False
            self.consumed = False

        async def __aiter__(self):
            # 底层传输流只能消费一次。SDK 在流式响应结束后会再次迭代同一响应以丢弃残余字节，
            # 若第二次仍新建解码器，就会从压缩流量中间重放并以「未到 EOF」误判为截断响应。
            if self.consumed:
                return
            self.consumed = True
            wire = decoded = 0
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            decoder = None
            try:
                declared = response.headers.get("content-length")
                if declared is not None:
                    if not declared.isdecimal():
                        raise ModelEgressError("invalid_response")
                    if len(declared) > 20 or int(declared) > wire_limit:
                        raise ModelEgressError("response_limit")
                if encoding == "gzip":
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                elif encoding == "deflate":
                    decoder = zlib.decompressobj()
                elif encoding != "identity":
                    raise ModelEgressError("invalid_response")
                async for chunk in response.stream:
                    wire += len(chunk)
                    if wire > wire_limit:
                        raise ModelEgressError("response_limit")
                    data = (
                        decoder.decompress(chunk, decoded_limit - decoded + 1) if decoder else chunk
                    )
                    decoded += len(data)
                    if decoded > decoded_limit:
                        raise ModelEgressError("response_limit")
                    if decoder and decoder.unused_data:
                        raise ModelEgressError("invalid_response")
                    yield data
                # 不调用无硬上限的flush；完整压缩流必须已到EOF，不能接受截断数据。
                if decoder and not decoder.eof:
                    raise ModelEgressError("invalid_response")
            except zlib.error:
                raise ModelEgressError("invalid_response") from None
            finally:
                await self.aclose()

        async def aclose(self):
            if not self.closed:
                self.closed = True
                await response.aclose()

    headers = response.headers.copy()
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    return httpx2.Response(
        response.status_code, headers=headers, stream=Stream(), extensions=response.extensions
    )
