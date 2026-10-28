"""托管模型出口：策略检查及逐请求DNS固定，不依赖可选模型SDK。"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.common.errors import require
from app.infrastructure.model_errors import ModelEgressError


def permitted_ip(value, *, internal=False):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_link_local or address.is_multicast or address.is_unspecified:
        return False
    if address.is_reserved and not address.is_loopback:
        return False
    if str(address) == "255.255.255.255":
        return False
    return address.is_global or (internal and (address.is_private or address.is_loopback))


def check_endpoint(settings, value):
    explicit = value in settings.agent_model_urls
    url = urlsplit(value)
    require(
        settings.agent_model_url_policy != "allowlist" or explicit,
        422,
        "invalid_input",
        "严格出口模式下，此地址须由运维加入允许列表",
    )
    require(url.scheme == "https" or explicit, 422, "invalid_input", "公网模型地址须使用HTTPS")
    try:
        address = ipaddress.ip_address(url.hostname or "")
    except ValueError:
        address = None
    if address is not None:
        require(
            permitted_ip(str(address), internal=explicit),
            422,
            "invalid_input",
            "模型目标地址不符合出口策略",
        )


def model_transport(settings, value, *, resolver=None, inner=None, max_response_bytes=None):
    """可选依赖延迟加载；每个模型上下文独享连接池和原始TLS身份。"""
    import httpx2

    check_endpoint(settings, value)
    origin = httpx2.URL(value)
    internal = value in settings.agent_model_urls

    class Transport(httpx2.AsyncBaseTransport):
        def __init__(self):
            self.inner = inner or httpx2.AsyncHTTPTransport(trust_env=False, retries=0)

        async def handle_async_request(self, request):
            if (request.url.scheme, request.url.host, request.url.port) != (
                origin.scheme,
                origin.host,
                origin.port,
            ):
                raise ModelEgressError()
            base_path = origin.path.rstrip("/")
            if (
                base_path
                and request.url.path != base_path
                and not request.url.path.startswith(base_path + "/")
            ):
                raise ModelEgressError()
            try:
                resolve = resolver or asyncio.get_running_loop().getaddrinfo
                addresses = await resolve(
                    origin.host,
                    origin.port or (443 if origin.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            except OSError:
                raise ModelEgressError("dns") from None
            ips = list(dict.fromkeys(item[4][0] for item in addresses))
            if not ips or not all(permitted_ip(ip, internal=internal) for ip in ips):
                raise ModelEgressError()
            # URL使用已验证IP，Host与SNI保持供应商域名；底层不能再次解析域名。
            chosen = next((ip for ip in ips if ":" not in ip), ips[0])
            headers = request.headers.copy()
            headers["host"] = origin.netloc.decode("ascii")
            headers["accept-encoding"] = "gzip, deflate"
            pinned = httpx2.Request(
                request.method,
                request.url.copy_with(host=chosen),
                headers=headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": origin.host},
            )
            response = await self.inner.handle_async_request(pinned)
            if 300 <= response.status_code < 400:
                await response.aclose()
                raise ModelEgressError()
            from app.infrastructure.model_response import bounded_response

            return bounded_response(
                response,
                wire_limit=min(
                    getattr(settings, "agent_model_response_wire_bytes", 4194304),
                    max_response_bytes or 16777216,
                ),
                decoded_limit=min(
                    getattr(settings, "agent_model_response_decoded_bytes", 4194304),
                    max_response_bytes or 16777216,
                ),
            )

        async def aclose(self):
            await self.inner.aclose()

    return Transport()
