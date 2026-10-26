"""真实Web路由和模板；隔离身份适配验证CSRF、归属入口和审批参数。"""

from datetime import UTC, datetime

from test_web_console import web  # noqa: F401 -- 复用既有隔离Web夹具。


def test_assistant_pages_escape_content_and_submit_exact_decision(web):  # noqa: F811
    original = web.application.call.side_effect
    calls = []
    task = {
        "id": "task-one",
        "version": 3,
        "prompt": "<script>alert(1)</script>",
        "status": "awaiting_confirmation",
        "cancel_requested": False,
        "updated_at": datetime.now(UTC),
        "pending": {
            "ticket": "server-ticket",
            "operation": "submit_record",
            "arguments": {"values": {"x": "<img>"}},
            "preview": {"change": "新建候选"},
        },
        "result": None,
    }

    async def operation(principal, name, payload, trace, **options):
        if name == "me":
            return {"name": "测试员工", "permissions": ["agent.run"]}
        if name.startswith("agent."):
            assert callable(options["authenticate"])
            calls.append((name, payload))
            if name == "agent.capabilities":
                return {"ready": True, "message": ""}
            if name == "agent.list":
                return {"items": [task], "next": None, "has_more": False}
            return task
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    listing = web.client.get("/console/assistant")
    assert listing.status_code == 200 and "开始处理" in listing.text
    assert "<script>alert(1)</script>" not in listing.text
    page = web.client.get("/console/assistant/task-one")
    assert page.status_code == 200 and "新建候选" in page.text
    assert "data-confirm-message" in page.text and "server-ticket" in page.text
    assert "<script>alert(1)</script>" not in page.text
    path = "/console/assistant/task-one/decision"
    assert web.client.post(path, data={"decision": "approve"}).status_code == 403
    assert not any(name == "agent.decide" for name, _ in calls)
    response = web.client.post(
        path,
        data={
            "csrf": "form-csrf",
            "version": "3",
            "proposal_id": "server-ticket",
            "decision": "approve",
        },
    )
    assert response.status_code == 303
    assert calls[-1] == (
        "agent.decide",
        {"identifier": "task-one", "version": 3, "proposal_id": "server-ticket", "approve": True},
    )
    assert (
        web.client.post(
            path,
            data={
                "csrf": "form-csrf",
                "version": "3",
                "proposal_id": "server-ticket",
                "decision": "yes",
            },
        ).status_code
        == 422
    )


def test_assistant_start_keeps_request_key_and_cancel_version(web):  # noqa: F811
    seen = []

    async def operation(principal, name, payload, trace, **options):
        seen.append((name, payload))
        return {"id": "task-one"}

    web.application.call.side_effect = operation
    result = web.client.post(
        "/console/assistant",
        data={"csrf": "form-csrf", "prompt": "查看数据", "request_key": "same-request-key"},
    )
    assert result.status_code == 303
    assert seen[-1] == ("agent.start", {"prompt": "查看数据", "request_key": "same-request-key"})
    result = web.client.post(
        "/console/assistant/task-one/cancel", data={"csrf": "form-csrf", "version": "4"}
    )
    assert result.status_code == 303
    assert seen[-1] == ("agent.cancel", {"identifier": "task-one", "version": 4})


def test_task_result_separates_receipts_from_model_claims(web):  # noqa: F811
    original = web.application.call.side_effect
    task = {
        "id": "task-result",
        "version": 1,
        "prompt": "核查操作结果",
        "status": "completed",
        "cancel_requested": False,
        "updated_at": datetime.now(UTC),
        "pending": None,
        "result": {
            "explanation": "<script>错误声称已全部成功</script>",
            "outcome_unknown": True,
            "receipts": [
                {"operation": "record", "status": "read", "result": {"name": "测试"}},
                {"operation": "access", "status": "denied"},
                {"operation": "submit_record", "status": "received", "result": {"id": "candidate"}},
            ],
        },
    }

    async def operation(principal, name, payload, trace, **options):
        if name == "me":
            return {"name": "测试员工", "permissions": ["agent.run"]}
        if name == "agent.get":
            return task
        return await original(principal, name, payload, trace)

    web.application.call.side_effect = operation
    response = web.client.get("/console/assistant/task-result")
    assert response.status_code == 200
    assert "执行结果待核实" in response.text
    assert "避免重复写入" in response.text
    assert "只读查询回执" in response.text
    assert "已拒绝，未执行" in response.text
    assert "已收到执行回执" in response.text
    assert "<script>错误声称已全部成功</script>" not in response.text
    assert '<details class="task-technical"><summary>' in response.text
    task["result"]["receipts"] = []
    response = web.client.get("/console/assistant/task-result")
    assert "本轮没有工具回执，不能据此认定完成了数据操作" in response.text
