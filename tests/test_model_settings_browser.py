"""真实浏览器→Web→应用→临时PG；不读取用户登录、不调用真实模型。"""

import asyncio
import os
import socket
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
import uvicorn
from cryptography.fernet import Fernet
from playwright.sync_api import expect, sync_playwright
from test_query_postgres import dataset, isolated_query_db  # noqa: F401

pytest.importorskip("office_agent")
from app.application.agent_service import AgentCoordinator
from app.entrypoints.web.session import SessionStore
from app.main import create_app
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.live


def test_browser_model_settings_end_to_end(request, tmp_path, monkeypatch):
    if os.environ.get("OFFICE_AGENT_BROWSER_TESTS") != "1":
        pytest.skip("需显式浏览器隔离验收开关")
    settings = request.getfixturevalue("isolated_query_db")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base = "http://127.0.0.1:" + str(listener.getsockname()[1])
    key = tmp_path / "model-key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    web_key = tmp_path / "web-key"
    web_key.write_bytes(b"isolated-test-web-secret-32-bytes")
    web_key.chmod(0o600)
    enabled = settings.model_copy(
        update={
            "public_url": base,
            "web_session_secret_file": web_key,
            "agent_secret_key_file": key,
            "agent_model_urls": ["https://model.invalid/v1"],
        }
    )
    store = SessionStore(enabled.web_session_secret, enabled.web_session_ttl)
    monkeypatch.setattr("app.entrypoints.web.console.SessionStore", lambda *args: store)
    cookie = store.create({"access_token": "synthetic-browser-token", "csrf": "form-csrf"})
    ready = threading.Event()
    shared, failures = {}, []

    async def serve():
        factory, _, _, admin, user, *_ = await dataset(settings)
        web = create_app(enabled)
        app = web.state.application
        app.verifier.verify_async = AsyncMock(return_value=admin)

        @asynccontextmanager
        async def model(config):
            yield FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("OK")]))

        app._agent = AgentCoordinator(app, model_context=model)
        server = uvicorn.Server(uvicorn.Config(web, log_level="critical", lifespan="off"))
        shared.update(server=server, app=app, user=user)
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
        except Exception as error:  # noqa: BLE001 -- 主线程必须收到服务失败。
            failures.append(error)
            ready.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(30) and not failures
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel=os.environ.get("OFFICE_TEST_BROWSER_CHANNEL") or None
            )
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": "office_web_session", "value": cookie, "url": base}])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/console/model-settings")
                expect(page.get_by_role("heading", name="智能助手配置", exact=True)).to_be_visible()
                page.locator("#model-url").select_option("https://model.invalid/v1")
                page.locator("#model-name").fill("fixture")
                page.locator("#model-secret-action").select_option("replace")
                page.locator("#model-secret").fill("synthetic-browser-model-key")
                page.get_by_label("已确认该模型端点获准处理助手任务数据（启用必需）").check()
                page.get_by_role("button", name="保存草稿", exact=True).click()
                expect(page.locator("[data-confirm-accept]")).to_be_visible()
                page.locator("[data-confirm-cancel]").click()
                expect(page.locator("dd").filter(has_text="暂无")).to_be_visible()
                page.get_by_role("button", name="保存草稿", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator(".notice")).to_contain_text("草稿已保存")
                expect(page.locator("#model-secret")).to_have_value("")
                assert "synthetic-browser-model-key" not in page.content()
                from pathlib import Path

                evidence = Path("runtime/agent-research/browser")
                evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(evidence / "model-settings.png"), full_page=True)
                page.get_by_role("button", name="启用修订 1", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator(".notice")).to_contain_text("配置已启用")
                page.get_by_label("确认可能计费，仅发送合成测试内容，不读取业务资料").check()
                page.get_by_role("button", name="测试草稿连接", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator(".notice")).to_contain_text("合成连接测试通过")
                page.get_by_role("button", name="停用助手", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator(".notice")).to_contain_text("智能助手已停用")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                shared["app"].verifier.verify_async.return_value = shared["user"]
                page.goto(base + "/console/dashboard")
                expect(page.locator("nav").get_by_role("link", name="智能助手配置")).to_have_count(
                    0
                )
                response = page.goto(base + "/console/model-settings")
                assert response.status == 403
                assert not errors, errors
            finally:
                browser.close()
    finally:
        if "server" in shared:
            shared["server"].should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive()
    assert not failures, failures
