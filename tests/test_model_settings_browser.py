"""真实浏览器→Web→应用→临时PG；不读取用户登录、不调用真实模型。"""

import asyncio
import os
import socket
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
import uvicorn
from app.entrypoints.web.responses import instance_cookie
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
            "agent_model_urls": [],
        }
    )
    store = SessionStore(enabled.web_session_secret, enabled.web_session_ttl)
    monkeypatch.setattr("app.entrypoints.web.console.SessionStore", lambda *args: store)
    cookie = store.create({"access_token": "synthetic-browser-token", "csrf": "form-csrf"})
    ready = threading.Event()
    shared, failures = {}, []

    async def wire(request):
        if request.url.host == "slow.invalid":
            await asyncio.sleep(0.3)
        if request.url.host == "missing.invalid":
            return httpx2.Response(404, json={"error": {"message": "private-provider-error"}})
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "fixture", "object": "model", "created": 1, "owned_by": "synthetic"},
                    {
                        "id": "second-fixture",
                        "object": "model",
                        "created": 1,
                        "owned_by": "synthetic",
                    },
                ],
            },
        )

    monkeypatch.setattr(
        "app.application.model_runtime.model_transport",
        lambda *args, **kwargs: httpx2.MockTransport(wire),
    )

    async def serve():
        factory, _, _, admin, user, *_ = await dataset(settings)
        web = create_app(enabled)
        app = web.state.application
        app.verifier.verify_async = AsyncMock(return_value=admin)
        original_call = app.call
        release_save = asyncio.Event()
        shared.update(
            loop=asyncio.get_running_loop(),
            release_save=release_save,
            save_started=threading.Event(),
        )

        async def delayed_save(*args, **kwargs):
            if args[1] == "agent.config.save" and shared.get("pause_save"):
                shared["pause_save"] = False
                shared["save_started"].set()
                await release_save.wait()
            return await original_call(*args, **kwargs)

        app.call = delayed_save

        @asynccontextmanager
        async def model(config):
            if config.model == "failed-model":
                raise ValueError("provider-secret-do-not-expose")
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
                context.add_cookies(
                    [
                        {
                            "name": instance_cookie(
                                "office_web_session", shared["app"].settings.public_url
                            ),
                            "value": cookie,
                            "url": base,
                        }
                    ]
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/console/model-settings")
                expect(page.get_by_role("heading", name="智能助手配置", exact=True)).to_be_visible()
                page.locator("#model-api").focus()
                page.keyboard.press("Tab")
                expect(page.locator("#model-url")).to_be_focused()
                page.locator("#model-url").fill("https://model.invalid/v1")
                page.locator("#model-secret").fill("synthetic-browser-model-key")
                page.get_by_role("button", name="获取模型列表", exact=True).click()
                expect(page.locator("#model-inline-result")).to_contain_text("已获取 2 个模型")
                expect(page.locator("#model-edit-status")).to_have_text("有未保存修改")
                page.locator("#model-name").focus()
                page.locator("#model-name").press("ArrowDown")
                page.locator("#model-name").press("Enter")
                expect(page.locator("#model-name")).to_have_value("fixture")
                expect(page.locator("#model-options")).to_be_hidden()
                page.get_by_role("button", name="测试连接", exact=True).click()
                page.locator("[data-confirm-cancel]").click()
                expect(page.locator("#model-inline-result")).not_to_contain_text("调用通过")
                page.get_by_role("button", name="测试连接", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator("#model-inline-result")).to_contain_text(
                    "当前输入的模型调用通过"
                )
                expect(page.locator("#model-edit-status")).to_have_text("有未保存修改")
                page.locator("#model-name").fill("manual-model")
                expect(page.locator("#model-inline-result")).to_contain_text("参数已变更")
                page.locator("#model-name").press("Escape")
                page.locator("#model-url").fill("https://missing.invalid/v1")
                page.get_by_role("button", name="获取模型列表", exact=True).click()
                expect(page.locator("#model-inline-result")).to_contain_text("手动填写模型ID")
                expect(page.locator("#model-name")).to_have_value("manual-model")
                assert "private-provider-error" not in page.content()
                page.locator("#model-url").fill("https://slow.invalid/v1")
                with page.expect_response(
                    lambda response: response.url.endswith("/console/model-settings/inspect")
                ):
                    page.get_by_role("button", name="获取模型列表", exact=True).click()
                    page.locator("#model-url").fill("https://model.invalid/v1")
                expect(page.locator("#model-inline-result")).to_contain_text("参数已变更")
                page.locator("#model-name").fill("fixture")
                page.locator("#model-name").press("Escape")
                page.get_by_label("已确认该模型端点获准处理助手任务数据（启用必需）").check()
                page.get_by_role("button", name="保存并启用", exact=True).click()
                page.locator("[data-confirm-cancel]").click()
                expect(page.locator("#model-secret")).to_have_value("synthetic-browser-model-key")
                page.get_by_role("button", name="保存并启用", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator(".notice")).to_contain_text("配置已保存并启用")
                expect(page.locator("#model-secret")).to_have_value("")
                expect(page.locator("#model-key-label")).to_have_text("已配置")
                assert "synthetic-browser-model-key" not in page.content()
                page.locator("#model-url").fill("https://other.invalid/v1")
                page.get_by_role("button", name="获取模型列表", exact=True).click()
                expect(page.locator("#model-inline-result")).to_contain_text("请确认沿用原密钥")
                page.get_by_role("button", name="还原修改", exact=True).click()
                expect(page.locator("#model-url")).to_have_value("https://model.invalid/v1")
                # 更换与显示不向脏表单快照加入秘密。
                page.get_by_role("button", name="更换", exact=True).click()
                page.locator("#model-secret").fill("new-synthetic-key")
                page.get_by_role("button", name="显示新输入的密钥", exact=True).click()
                assert page.locator("#model-secret").get_attribute("type") == "text"
                assert not page.evaluate(
                    "Array.from(OfficeForms.captureDirty(document).values()).join('').includes('new-synthetic-key')"
                )
                page.get_by_role("button", name="还原修改", exact=True).click()
                expect(page.locator("#model-secret")).to_have_value("")
                page.locator("#model-advanced > summary").click()
                page.locator("#model-auth-mode").select_option("none")
                expect(page.locator("#model-secret-field")).to_be_hidden()
                expect(page.locator("#model-secret")).to_be_disabled()
                page.get_by_role("button", name="还原修改", exact=True).click()
                page.locator("#model-name").fill("failed-model")
                page.locator("#model-name").press("Escape")
                page.get_by_role("button", name="测试连接", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator("#model-inline-result")).to_contain_text("未收到兼容的模型响应")
                assert "provider-secret-do-not-expose" not in page.content()
                page.get_by_role("button", name="还原修改", exact=True).click()
                from pathlib import Path

                evidence = Path("runtime/model-setup-research/browser")
                evidence.mkdir(parents=True, exist_ok=True)
                if page.locator("#model-advanced").get_attribute("open") is not None:
                    page.locator("#model-advanced > summary").click()
                assert page.locator("#model-probe").bounding_box()["y"] < 1000
                page.screenshot(path=str(evidence / "model-settings.png"), full_page=True)
                page.emulate_media(reduced_motion="reduce")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.locator(".model-details > summary").click()
                page.get_by_role("button", name="停用助手", exact=True).click()
                page.locator("[data-confirm-accept]").click()
                expect(page.locator(".notice")).to_contain_text("智能助手已停用")
                plain = browser.new_context(java_script_enabled=False)
                plain.add_cookies(
                    [
                        {
                            "name": instance_cookie(
                                "office_web_session", shared["app"].settings.public_url
                            ),
                            "value": cookie,
                            "url": base,
                        }
                    ]
                )
                plain_page = plain.new_page()
                response = plain_page.goto(base + "/console/model-settings")
                assert response.status == 200
                expect(
                    plain_page.get_by_role("heading", name="智能助手配置", exact=True)
                ).to_be_visible()
                plain_page.locator("#model-advanced > summary").click()
                expect(plain_page.locator("#model-auth")).to_be_visible()
                expect(plain_page.locator("#model-secret")).to_have_value("")
                plain.close()
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
            shared["loop"].call_soon_threadsafe(shared["release_save"].set)
            shared["server"].should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive()
    assert not failures, failures
