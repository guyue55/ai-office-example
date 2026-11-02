"""模型专用DoH：合成DNS及HTTP，不使用密钥、不调用供应商。"""

import asyncio
import socket
from types import SimpleNamespace

import pytest

httpx2 = pytest.importorskip("httpx2")
from app.infrastructure.model_dns import DohResolver
from app.infrastructure.model_egress import ModelEgressError, model_transport


def response(request, *, ttl=60):
    kind = int(request.url.params["type"])
    return {
        "Status": 0,
        "Question": [{"name": "model.invalid.", "type": kind}],
        "Answer": [
            {
                "name": "model.invalid.",
                "type": kind,
                "TTL": ttl,
                "data": "8.8.8.8" if kind == 1 else "2001:4860:4860::8888",
            }
        ],
    }


def test_resolver_queries_both_families_and_obeys_bounded_ttl():
    async def scenario():
        calls = []
        clock = [0]

        async def serve(request):
            calls.append(request)
            return httpx2.Response(200, json=response(request, ttl=3600))

        client = httpx2.AsyncClient(transport=httpx2.MockTransport(serve))
        resolver = DohResolver("https://dns.invalid/query", client=client, clock=lambda: clock[0])
        try:
            records = await resolver("model.invalid", 443, type=socket.SOCK_STREAM)
            assert {r[4][0] for r in records} == {"8.8.8.8", "2001:4860:4860::8888"}
            assert len(calls) == 2
            await resolver("model.invalid", 8443, type=socket.SOCK_STREAM)
            assert len(calls) == 2
            clock[0] = 61
            await resolver("model.invalid", 443, type=socket.SOCK_STREAM)
            assert len(calls) == 4
        finally:
            await resolver.aclose()
        assert client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["question", "address", "status", "redirect", "oversize", "truncated", "timeout"]
)
def test_resolver_rejects_invalid_or_unbounded_answers(failure):
    async def scenario():
        async def serve(request):
            data = response(request)
            if failure == "timeout":
                raise httpx2.ReadTimeout("synthetic-secret-must-not-escape")
            if failure == "redirect":
                return httpx2.Response(302, headers={"location": "https://other.invalid"})
            if failure == "oversize":
                return httpx2.Response(200, content=b"x" * 65537)
            if failure == "question":
                data["Question"][0]["name"] = "other.invalid"
            if failure == "address":
                data["Answer"][0]["data"] = "not-an-ip"
            if failure == "status":
                data["Status"] = 3
            if failure == "truncated":
                data["TC"] = True
            return httpx2.Response(200, json=data)

        resolver = DohResolver(
            "https://dns.invalid/query",
            client=httpx2.AsyncClient(transport=httpx2.MockTransport(serve)),
        )
        try:
            with pytest.raises(ModelEgressError) as caught:
                await resolver("model.invalid", 443, type=socket.SOCK_STREAM)
            assert caught.value.category == "dns"
            assert "synthetic-secret" not in str(caught.value)
        finally:
            await resolver.aclose()

    asyncio.run(scenario())


def test_doh_is_scoped_and_still_blocks_private_or_fake_ips(monkeypatch):
    import app.infrastructure.model_egress as egress

    async def scenario():
        calls, seen, closed = [], [], []
        addresses = ["8.8.8.8"]

        class Resolver:
            def __init__(self, url):
                pass

            async def __call__(self, host, port, **kwargs):
                calls.append(host)
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in addresses]

            async def aclose(self):
                closed.append(True)

        monkeypatch.setattr(egress, "DohResolver", Resolver)
        config = SimpleNamespace(
            agent_model_urls=[],
            agent_model_url_policy="public",
            agent_model_doh_url="https://dns.invalid/query",
            agent_model_doh_domains=["model.invalid"],
        )

        async def serve(request):
            seen.append(request)
            return httpx2.Response(200, text="ok")

        transport = model_transport(
            config, "https://model.invalid/v1", inner=httpx2.MockTransport(serve)
        )
        async with httpx2.AsyncClient(transport=transport) as client:
            assert (await client.get("https://model.invalid/v1/responses")).status_code == 200
            assert seen[0].url.host == "8.8.8.8"
            assert seen[0].headers["host"] == "model.invalid"
            assert seen[0].extensions["sni_hostname"] == "model.invalid"
            for values in [["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["198.18.1.2"], ["2001:2::1"]]:
                addresses[:] = values
                with pytest.raises(ModelEgressError):
                    await client.get("https://model.invalid/v1/responses")
        assert len(seen) == 1 and closed == [True]
        calls.clear()
        transport = model_transport(
            config,
            "https://other.invalid",
            inner=httpx2.MockTransport(serve),
            resolver=lambda *args, **kwargs: asyncio.sleep(
                0, result=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
            ),
        )
        async with httpx2.AsyncClient(transport=transport) as client:
            assert (await client.get("https://other.invalid/")).status_code == 200
        assert not calls

    asyncio.run(scenario())


def test_doh_configuration_requires_explicit_valid_domain_scope(tmp_path):
    from app.infrastructure.config import Settings

    base = {
        "database_user": "fixture",
        "database_password_file": tmp_path / "unused",
        "issuer": "https://id.invalid",
        "audience": "https://app.invalid/mcp",
        "public_url": "https://app.invalid",
    }
    valid = {
        "agent_model_doh_scope": "domains",
        "agent_model_doh_url": "https://dns.invalid/query",
        "agent_model_doh_domains": ["model.invalid"],
    }
    assert Settings(**base, **valid).agent_model_doh_domains == ["model.invalid"]
    for changes in [
        {"agent_model_doh_url": "http://dns.invalid/query"},
        {"agent_model_doh_url": "https://user:secret@dns.invalid/query"},
        {"agent_model_doh_domains": []},
        {"agent_model_doh_domains": ["*.invalid"]},
        {"agent_model_doh_domains": ["127.0.0.1"]},
        {"agent_model_doh_url": "https://dns.invalid/query?x=y"},
    ]:
        with pytest.raises(ValueError):
            Settings(**base, **{**valid, **changes})


def test_resolver_cancellation_closes_both_queries():
    async def scenario():
        started, closed = [], []
        ready = asyncio.Event()

        async def serve(request):
            started.append(True)
            if len(started) == 2:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        client = httpx2.AsyncClient(transport=httpx2.MockTransport(serve))
        resolver = DohResolver("https://dns.invalid/query", client=client)
        task = asyncio.create_task(resolver("model.invalid", 443))
        await asyncio.wait_for(ready.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(closed) == 2 and not resolver.lock.locked()
        await resolver.aclose()
        assert client.is_closed

    asyncio.run(scenario())


def test_cname_chain_and_zero_ttl():
    async def scenario():
        calls = []

        async def serve(request):
            calls.append(True)
            data = response(request, ttl=0)
            data["Answer"][0]["name"] = "alias.invalid."
            data["Answer"].append(
                {"name": "model.invalid.", "type": 5, "TTL": 60, "data": "alias.invalid."}
            )
            return httpx2.Response(200, json=data)

        resolver = DohResolver(
            "https://dns.invalid/query",
            client=httpx2.AsyncClient(transport=httpx2.MockTransport(serve)),
        )
        try:
            assert await resolver("model.invalid", 443)
            assert await resolver("model.invalid", 443)
            assert len(calls) == 4
        finally:
            await resolver.aclose()

    asyncio.run(scenario())


def test_new_dns_categories_are_valid_contract_results():
    from app.application.model_test_errors import failure
    from app.modules.agent.model_configuration import ModelTestResult

    for category in ("dns_fake_ip", "dns_address"):
        result = ModelTestResult(revision=1, elapsed_ms=1, **failure(ModelEgressError(category)))
        assert result.category == category and not result.passed


def test_explicit_internal_permission_does_not_allow_benchmark_ranges():
    from app.infrastructure.model_egress import permitted_ip

    assert permitted_ip("10.0.0.2", internal=True)
    for ip in ("198.18.0.1", "198.19.255.254", "2001:2::1", "::ffff:198.18.1.2"):
        assert not permitted_ip(ip, internal=True)


def test_automatic_doh_needs_no_vendor_domain_list_and_preserves_old_scope(tmp_path):
    from app.infrastructure.config import Settings

    base = {
        "database_user": "fixture",
        "database_password_file": tmp_path / "unused",
        "issuer": "https://id.invalid",
        "audience": "https://app.invalid/mcp",
        "public_url": "https://app.invalid",
    }
    automatic = Settings(**base, agent_model_doh_url="https://dns.invalid/query")
    assert automatic.agent_model_doh_scope == "auto" and automatic.agent_model_doh_domains == []
    legacy = Settings(
        **base,
        agent_model_doh_url="https://dns.invalid/query",
        agent_model_doh_domains=["model.invalid"],
    )
    assert legacy.agent_model_doh_scope == "domains"


def test_automatic_resolution_for_any_public_vendor_and_fail_closed(monkeypatch):
    import app.infrastructure.model_egress as egress

    async def scenario():
        calls, system_calls = [], []
        ips = ["198.18.1.2"]
        failing = [False]

        class Resolver:
            def __init__(self, url):
                pass

            async def __call__(self, host, port, **kwargs):
                calls.append(host)
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

            async def aclose(self):
                pass

        async def system(host, port, **kwargs):
            system_calls.append(host)
            if failing[0]:
                raise socket.gaierror("synthetic")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

        monkeypatch.setattr(egress, "DohResolver", Resolver)
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", system)
        config = SimpleNamespace(
            agent_model_urls=[],
            agent_model_url_policy="public",
            agent_model_doh_url="https://dns.invalid/query",
            agent_model_doh_domains=[],
            agent_model_doh_scope="auto",
        )
        for host in ("vendor-one.invalid", "vendor-two.invalid"):
            transport = model_transport(
                config,
                "https://" + host,
                inner=httpx2.MockTransport(lambda request: httpx2.Response(200)),
            )
            async with httpx2.AsyncClient(transport=transport) as client:
                assert (await client.get("https://" + host)).status_code == 200
        assert calls == ["vendor-one.invalid", "vendor-two.invalid"]
        calls.clear()
        ips[:] = ["8.8.8.8"]
        transport = model_transport(
            config,
            "https://normal.invalid",
            inner=httpx2.MockTransport(lambda request: httpx2.Response(200)),
        )
        async with httpx2.AsyncClient(transport=transport) as client:
            assert (await client.get("https://normal.invalid")).status_code == 200
        assert not calls
        for values in (["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["198.18.1.2", "169.254.169.254"]):
            ips[:] = values
            transport = model_transport(
                config,
                "https://private.invalid",
                inner=httpx2.MockTransport(lambda request: httpx2.Response(200)),
            )
            async with httpx2.AsyncClient(transport=transport) as client:
                with pytest.raises(ModelEgressError):
                    await client.get("https://private.invalid")
        assert not calls
        failing[0] = True
        for host in ("public.invalid", "model.lan"):
            transport = model_transport(
                config,
                "https://" + host,
                inner=httpx2.MockTransport(lambda request: httpx2.Response(200)),
            )
            async with httpx2.AsyncClient(transport=transport) as client:
                if host.endswith(".lan"):
                    with pytest.raises(ModelEgressError):
                        await client.get("https://" + host)
                else:
                    assert (await client.get("https://" + host)).status_code == 200
        assert calls == ["public.invalid"]

    asyncio.run(scenario())


def test_automatic_doh_failure_never_sends_model_request(monkeypatch):
    import app.infrastructure.model_egress as egress

    async def scenario():
        sent = []

        class Resolver:
            def __init__(self, url):
                pass

            async def __call__(self, host, port, **kwargs):
                raise ModelEgressError("dns")

            async def aclose(self):
                pass

        async def system(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.2", port))]

        monkeypatch.setattr(egress, "DohResolver", Resolver)
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", system)
        config = SimpleNamespace(
            agent_model_urls=[],
            agent_model_url_policy="public",
            agent_model_doh_url="https://dns.invalid/query",
            agent_model_doh_domains=[],
            agent_model_doh_scope="auto",
        )

        async def serve(request):
            sent.append(request)
            return httpx2.Response(200)

        async with httpx2.AsyncClient(
            transport=model_transport(
                config, "https://any-vendor.invalid", inner=httpx2.MockTransport(serve)
            )
        ) as client:
            with pytest.raises(ModelEgressError) as caught:
                await client.get("https://any-vendor.invalid")
            assert caught.value.category == "dns"
        assert sent == []

    asyncio.run(scenario())


def test_automatic_mode_keeps_explicit_internal_model_on_system_dns(monkeypatch):
    async def scenario():
        async def system(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", port))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", system)
        config = SimpleNamespace(
            agent_model_urls=["https://model.company.invalid"],
            agent_model_url_policy="public",
            agent_model_doh_url="https://dns.invalid/query",
            agent_model_doh_domains=[],
            agent_model_doh_scope="auto",
        )
        transport = model_transport(
            config,
            "https://model.company.invalid",
            inner=httpx2.MockTransport(lambda request: httpx2.Response(200)),
        )
        async with httpx2.AsyncClient(transport=transport) as client:
            assert (await client.get("https://model.company.invalid")).status_code == 200

    asyncio.run(scenario())
