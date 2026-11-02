"""只读订阅，初始鉴权与终态快照；不发起新的Agent任务。"""

from test_assistant_workspace_web import install_workspace
from test_web_console import web  # noqa: F401


def test_event_stream_is_authorized_snapshot_and_never_executes_task(web):  # noqa: F811
    _conversation, turn, calls, state = install_workspace(web)
    turn.update(
        status="completed", pending=None, result={"explanation": "合成结果", "receipts": []}
    )
    response = web.client.get("/console/assistant-ui/runs/task-one/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert "event: snapshot" in response.text and "合成结果" in response.text
    assert "event: end" in response.text
    assert not any(name in {"agent.start", "agent.decide", "agent.cancel"} for name, _ in calls)
    state["allowed"] = False
    assert web.client.get("/console/assistant-ui/runs/task-one/events").status_code == 403


def test_stream_rechecks_authorization_and_stops_without_reexecution():
    import asyncio
    import json

    from app.common.errors import DomainError
    from app.entrypoints.web.assistant_events import snapshots

    class Request:
        async def is_disconnected(self):
            return False

    async def scenario():
        calls = []

        async def denied():
            calls.append("read")
            raise DomainError(403, "permission_denied", "denied")

        messages = [
            message
            async for message in snapshots(
                Request(),
                {"status": "running"},
                denied,
                lambda value: json.dumps(value).encode(),
                interval=0,
            )
        ]
        assert len(calls) == 1
        assert b"event: access_lost" in messages[-1]
        assert sum(b"event: snapshot" in message for message in messages) == 1

    asyncio.run(scenario())


def test_subscription_lease_is_bounded_and_releasing_twice_does_not_free_other_tabs():
    import pytest
    from app.common.errors import DomainError
    from app.entrypoints.web.assistant_events import Subscriptions

    slots = Subscriptions()
    release = slots.acquire("run")
    slots.acquire("run")
    slots.acquire("run")
    with pytest.raises(DomainError):
        slots.acquire("run")
    release()
    release()
    assert slots.runs["run"] == 2
