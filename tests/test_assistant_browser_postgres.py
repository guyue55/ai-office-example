"""独立Chrome→真实BFF／应用／Agent→临时PG；仅身份与模型使用确定性夹具。"""

import asyncio
import json
import os
import socket
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import uvicorn
from app.entrypoints.web.responses import instance_cookie
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytest.importorskip("office_agent", reason="需可选Agent依赖")

from app.application.agent_service import AgentCoordinator
from app.entrypoints.web.session import SessionStore
from app.infrastructure.models import Audit, Record
from app.main import create_app
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.live


@pytest.mark.parametrize("streamed", [False, True])
def test_browser_approval_commits_one_candidate_with_agent_audit(
    request, tmp_path, monkeypatch, streamed
):
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需OFFICE_AGENT_BROWSER_TESTS=1与OFFICE_QUERY_PG_TESTS=1")
    settings = request.getfixturevalue("isolated_query_db")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    secret = tmp_path / "web-secret"
    secret.write_bytes(b"isolated-browser-database-secret-32")
    secret.chmod(0o600)
    config = tmp_path / "agent.toml"
    config.write_text(
        f'service_url="{base}"\nmodel_url="https://model.invalid/v1"\n'
        'model="fixture"\napi_key_env="ISOLATED_MODEL_KEY"\napproved_model_data=true\n'
    )
    enabled = settings.model_copy(
        update={
            "public_url": base,
            "allow_lan_http_demo": True,
            "web_session_secret_file": secret,
            "agent_config_file": config,
        }
    )
    store = SessionStore(enabled.web_session_secret, enabled.web_session_ttl)
    monkeypatch.setattr("app.entrypoints.web.console.SessionStore", lambda *args: store)
    cookie = store.create({"access_token": "isolated-browser-token", "csrf": "form-csrf"})
    ready = threading.Event()
    shared = {}
    failures = []

    async def serve():
        factory, _seed, _call, _admin, user, project, *_ = await dataset(settings)
        stream_release = asyncio.Event()
        shared["release_stream"] = stream_release

        def model(messages, info):
            returns = [
                part for m in messages for part in m.parts if isinstance(part, ToolReturnPart)
            ]
            if not returns:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "prepare_write",
                            {
                                "operation": "submit_record",
                                "arguments": {
                                    "project_id": project["id"],
                                    "schema_revision": 1,
                                    "values": {"v": "0.40", "tag": "browser-acceptance"},
                                },
                            },
                            tool_call_id="preview",
                        )
                    ]
                )
            if returns[-1].tool_name == "prepare_write":
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "execute_write",
                            {"ticket": returns[-1].content["ticket"]},
                            tool_call_id="write",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("候选已上报，尚未业务确认。")])

        async def stream_model(messages, info):
            response = model(messages, info)
            for index, part in enumerate(response.parts):
                if isinstance(part, TextPart):
                    yield "候选已上报"
                    await asyncio.wait_for(stream_release.wait(), 20)
                    yield "，尚未业务确认。"
                elif isinstance(part, ToolCallPart):
                    yield {
                        index: DeltaToolCall(
                            name=part.tool_name,
                            json_args=json.dumps(part.args),
                            tool_call_id=part.tool_call_id,
                        )
                    }

        @asynccontextmanager
        async def models(_config):
            yield FunctionModel(stream_function=stream_model) if streamed else FunctionModel(model)

        web = create_app(enabled)
        app = web.state.application
        app.verifier.verify_async = AsyncMock(return_value=user)
        app._agent = AgentCoordinator(app, model_context=models)

        async def facts():
            async with factory() as db:
                rows = (
                    await db.scalars(select(Record).where(Record.project_id == project["id"]))
                ).all()
                written = [row for row in rows if row.values.get("tag") == "browser-acceptance"]
                audits = (
                    await db.scalars(select(Audit).where(Audit.action == "record.created"))
                ).all()
                return written, [
                    row for row in audits if row.resource_id in {r.id for r in written}
                ]

        server = uvicorn.Server(uvicorn.Config(web, log_level="critical", lifespan="off"))
        shared.update(server=server, loop=asyncio.get_running_loop(), facts=facts)
        ready.set()
        try:
            await server.serve(sockets=[listener])
        finally:
            await app._agent.close()
            await app.sessions.kw["bind"].dispose()
            await app.verifier.aclose()
            await factory.kw["bind"].dispose()

    def run():
        try:
            asyncio.run(serve())
        except Exception as error:  # noqa: BLE001 -- 线程边界回传所有运行异常，主线程断言失败。
            failures.append(error)
            ready.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(30), "隔离应用启动超时"
        assert not failures, failures
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
            )
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                context.add_cookies(
                    [
                        {
                            "name": instance_cookie("office_web_session", enabled.public_url),
                            "value": cookie,
                            "url": base,
                        }
                    ]
                )
                page = context.new_page()
                browser_errors = []
                page.on("pageerror", lambda error: browser_errors.append(str(error)))
                page.goto(base + "/console/assistant/workspace")
                field = page.locator("#assistant-input")
                expect(field).to_be_enabled(timeout=15000)
                field.fill("提交合成候选数据，先给出预览，不要业务确认。")
                page.get_by_role("button", name="发送", exact=True).click()
                expect(page.get_by_role("button", name="批准本次操作", exact=True)).to_be_visible(
                    timeout=15000
                )

                def facts():
                    return asyncio.run_coroutine_threadsafe(
                        shared["facts"](), shared["loop"]
                    ).result(10)

                assert facts() == ([], [])  # 预览没有写入数据库。
                page.get_by_role("button", name="批准本次操作", exact=True).click()
                page.locator("[data-confirm-cancel]").click()
                assert facts() == ([], [])  # 取消确认同样没有写入。
                page.get_by_role("button", name="批准本次操作", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                if streamed:
                    expect(page.locator("[data-progress-text]")).to_contain_text(
                        "候选已上报", timeout=15000
                    )
                    shared["loop"].call_soon_threadsafe(shared["release_stream"].set)
                expect(page.locator(".assistant-markdown")).to_contain_text(
                    "候选已上报", timeout=15000
                )
                rows, audits = facts()
                assert len(rows) == len(audits) == 1
                assert rows[0].values["v"] == "0.40"
                assert audits[0].detail["agent_run_id"]
                expect(page.locator(".assistant-receipt")).to_contain_text("browser-acceptance")
                evidence = Path("runtime/agent-research/browser")
                evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(evidence / "assistant-real-database.png"), full_page=True)
                assert not browser_errors
            finally:
                browser.close()
    finally:
        if "server" in shared:
            shared["server"].should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive()
    assert not failures, failures
