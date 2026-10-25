"""通过可观察回执检验审批、冻结、失败和拒绝，不依赖真实员工凭证。"""

import asyncio
import copy

import pytest

from office_agent.bridge import SDKBridge
from office_agent.config import Limits
from office_agent.gateway import Gateway
from office_agent.policy import AgentError


class Client:
    def __init__(self):
        self.calls = []
        self.identity = {"id": "employee", "access_revision": 1, "permissions": ["record.create"]}
        self.closed = False
        self.fail_write = False

    def me(self):
        return copy.deepcopy(self.identity)

    def projects(self, **params):
        return {"items": [{"id": "p"}], "next": "more", "complete": False}

    def preview_data_change(self, change):
        return {"change": change, "confirmation_digest": "a" * 64, "after": {"name": "新项目"}}

    def apply_data_change(self, change, confirmation_digest):
        self.calls.append((change, confirmation_digest))
        if self.fail_write:
            raise OSError("secret-access-token")
        return {"operation": "project.create", "result": {"id": "p"}}

    def close(self):
        self.closed = True


def test_frozen_ticket_and_one_execution():
    async def scenario():
        client = Client()
        async with SDKBridge(lambda: client) as bridge:
            gateway = Gateway(bridge, Limits())
            await gateway.start()
            change = {"operation": "project.create", "payload": {"name": "新项目"}}
            ticket = await gateway.prepare("data", {"change": change})
            change["payload"]["name"] = "被修改"
            view = gateway.proposal(ticket)
            assert view.arguments["change"]["payload"]["name"] == "新项目"
            view.arguments["change"]["payload"]["name"] = "再次修改"
            assert not client.calls
            result = await gateway.execute(ticket)
            assert result["operation"] == "project.create"
            assert client.calls[0][0]["payload"]["name"] == "新项目"
            with pytest.raises(AgentError, match="已使用"):
                await gateway.execute(ticket)
            assert len(client.calls) == 1
        assert client.closed

    asyncio.run(scenario())


def test_identity_or_permissions_change_invalidates_ticket():
    async def scenario():
        client = Client()
        async with SDKBridge(lambda: client) as bridge:
            gateway = Gateway(bridge, Limits())
            await gateway.start()
            ticket = await gateway.prepare("data", {"change": {"operation": "project.create"}})
            client.identity["permissions"] = []
            with pytest.raises(AgentError, match="身份或权限"):
                await gateway.execute(ticket)
            assert not client.calls

    asyncio.run(scenario())


def test_unknown_write_is_not_replayed_or_leaked():
    async def scenario():
        client = Client()
        client.fail_write = True
        async with SDKBridge(lambda: client) as bridge:
            gateway = Gateway(bridge, Limits())
            await gateway.start()
            ticket = await gateway.prepare("data", {"change": {"operation": "project.create"}})
            with pytest.raises(AgentError) as caught:
                await gateway.execute(ticket)
            assert caught.value.outcome_unknown
            assert "secret-access-token" not in str(caught.value)
            with pytest.raises(AgentError):
                await gateway.execute(ticket)
            assert len(client.calls) == 1

    asyncio.run(scenario())


def test_no_arbitrary_method_and_paging_preserved():
    async def scenario():
        async with SDKBridge(Client) as bridge:
            gateway = Gateway(bridge, Limits())
            await gateway.start()
            result = await gateway.read("projects", {"limit": 10})
            assert result["next"] == "more" and result["complete"] is False
            for operation in ["request", "oauth", "close", "__class__"]:
                with pytest.raises(AgentError):
                    await gateway.read(operation, {})
            with pytest.raises(AgentError):
                await gateway.read("projects", {"service_url": "https://evil.example"})
            with pytest.raises(AgentError):
                await gateway.read("projects", {"limit": 10000})

    asyncio.run(scenario())


def test_switched_employee_cannot_mix_read_data():
    async def scenario():
        client = Client()
        async with SDKBridge(lambda: client) as bridge:
            gateway = Gateway(bridge, Limits())
            await gateway.start()
            client.identity["id"] = "another-employee"
            with pytest.raises(AgentError, match="身份或权限"):
                await gateway.read("projects", {})
            assert not gateway.receipts

    asyncio.run(scenario())


def test_paging_and_proposal_budgets_are_enforced():
    async def scenario():
        async with SDKBridge(Client) as bridge:
            gateway = Gateway(bridge, Limits(pages=1, pending_writes=1))
            await gateway.start()
            await gateway.read("projects", {})
            with pytest.raises(AgentError, match="分页预算"):
                await gateway.read("projects", {})
            await gateway.prepare("data", {"change": {"operation": "project.create"}})
            with pytest.raises(AgentError, match="上限"):
                await gateway.prepare("data", {"change": {"operation": "project.create"}})

    asyncio.run(scenario())


def test_unrelated_global_revision_does_not_interrupt_own_reads():
    async def scenario():
        client = Client()
        async with SDKBridge(lambda: client) as bridge:
            gateway = Gateway(bridge, Limits())
            await gateway.start()
            client.identity["access_revision"] += 1
            assert (await gateway.read("projects", {}))["next"] == "more"

    asyncio.run(scenario())
