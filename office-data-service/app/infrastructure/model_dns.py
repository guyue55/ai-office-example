"""仅供显式模型域名使用的有界DoH JSON解析；不修改全局DNS。"""

import asyncio
import ipaddress
import json
import socket
import time
from collections import OrderedDict

from app.infrastructure.model_errors import ModelEgressError
from app.infrastructure.model_response import bounded_response


def dns_name(value):
    if not isinstance(value, str):
        raise TypeError("域名格式无效")
    value = value.rstrip(".").encode("idna").decode("ascii").lower()
    labels = value.split(".")
    if len(value) > 253 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in label)
        for label in labels
    ):
        raise ValueError("域名格式无效")
    return value


class DohResolver:
    def __init__(self, url, *, client=None, clock=time.monotonic):
        # 可选HTTP依赖延迟导入，普通数据镜像无需安装Agent。
        import httpx2

        self.url = url
        self.client = client or httpx2.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=5,
            limits=httpx2.Limits(max_connections=4, max_keepalive_connections=2),
            transport=httpx2.AsyncHTTPTransport(trust_env=False, retries=0),
        )
        self.clock = clock
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()

    async def query(self, host, kind):
        async with self.client.stream(
            "GET",
            self.url,
            params={"name": host, "type": kind},
            headers={"accept": "application/dns-json", "accept-encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                raise ValueError("DNS查询失败")
            bounded = bounded_response(response, wire_limit=65536, decoded_limit=65536)
            try:
                data = json.loads(await bounded.aread())
            finally:
                await bounded.aclose()
        if (
            not isinstance(data, dict)
            or type(data.get("Status")) is not int
            or data["Status"] != 0
            or data.get("TC", False) is not False
        ):
            raise ValueError("DNS响应无效")
        questions = data.get("Question")
        if (
            not isinstance(questions, list)
            or len(questions) != 1
            or not isinstance(questions[0], dict)
            or dns_name(questions[0].get("name")) != host
            or questions[0].get("type") != kind
        ):
            raise ValueError("DNS问题不匹配")
        answers = data.get("Answer", [])
        if not isinstance(answers, list) or len(answers) > 64:
            raise ValueError("DNS答案过多")
        aliases, ttl = {}, 60
        for item in answers:
            if not isinstance(item, dict) or type(item.get("TTL")) is not int or item["TTL"] < 0:
                raise ValueError("DNS记录无效")
            ttl = min(ttl, item["TTL"])
            if item.get("type") == 5:
                owner = dns_name(item.get("name"))
                if owner in aliases:
                    raise ValueError("重复别名")
                aliases[owner] = dns_name(item.get("data"))
        chain, name = {host}, host
        while name in aliases:
            name = aliases[name]
            if name in chain or len(chain) >= 8:
                raise ValueError("DNS别名循环")
            chain.add(name)
        ips = []
        for item in answers:
            if item.get("type") == kind:
                if dns_name(item.get("name")) not in chain:
                    raise ValueError("DNS记录不匹配")
                ip = ipaddress.ip_address(item.get("data"))
                if ip.version != (4 if kind == 1 else 6):
                    raise ValueError("DNS地址族不匹配")
                ips.append(str(ip))
        return ips, ttl

    async def __call__(self, host, port, **kwargs):
        try:
            host = dns_name(host)
            async with self.lock:
                cached = self.cache.get(host)
                if cached and self.clock() < cached[1]:
                    ips = cached[0]
                    self.cache.move_to_end(host)
                else:
                    started = self.clock()
                    async with asyncio.timeout(5):
                        results = await asyncio.gather(
                            self.query(host, 1), self.query(host, 28), return_exceptions=True
                        )
                    successful = []
                    for result in results:
                        if isinstance(result, BaseException):
                            raise ModelEgressError("dns")
                        successful.append(result)
                    ips = list(dict.fromkeys(ip for values, ttl in successful for ip in values))
                    if not ips or len(ips) > 64:
                        raise ValueError("DNS无可用地址")
                    ttl = min(ttl for values, ttl in successful)
                    if ttl:
                        self.cache[host] = (ips, started + ttl)
                        self.cache.move_to_end(host)
                        while len(self.cache) > 64:
                            self.cache.popitem(last=False)
            return [
                (
                    socket.AF_INET6 if ":" in ip else socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (ip, port, 0, 0) if ":" in ip else (ip, port),
                )
                for ip in ips
            ]
        except Exception:  # noqa: BLE001 -- 不回显解析器、查询正文或网络异常。
            raise ModelEgressError("dns") from None

    async def aclose(self):
        self.cache.clear()
        await self.client.aclose()
