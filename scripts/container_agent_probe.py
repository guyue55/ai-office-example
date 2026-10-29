"""仅由docker_smoke挂入隔离容器；合成身份及loopback模型，不用于真实实例。"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from app.application.agent_service import AgentCoordinator
from app.infrastructure.config import Settings
from app.main import create_app
from app.modules.access.authentication import Principal


class Model(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        assert self.path == "/v1/chat/completions"
        assert self.headers["Authorization"] == "Bearer synthetic-model-key"
        self.rfile.read(int(self.headers["Content-Length"]))
        data = json.dumps(
            {
                "id": "synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "synthetic",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1"
    settings = Settings.load().model_copy(
        update={
            "agent_secret_key_file": Path("/etc/office/synthetic_model_key"),
            "agent_model_urls": [url],
            "agent_model_url_policy": "allowlist",
        }
    )
    web = create_app(settings)
    app = web.state.application
    identity = json.loads(Path("/etc/office/identity.json").read_text())
    principal = Principal(
        settings.issuer,
        identity["subject"],
        {"iat": int(time.time()), "exp": int(time.time()) + 3600, "azp": "synthetic"},
    )
    app.verifier.verify_async = AsyncMock(return_value=principal)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=web),
            base_url=settings.public_url,
            headers={"Authorization": "Bearer synthetic-token"},
        ) as client:
            path = "/api/v2/agent/configuration"
            state = (await client.get(path)).json()["data"]
            response = await client.post(
                path + "/draft",
                json={
                    "version": state["version"],
                    "draft": {
                        "model_url": url,
                        "model": "synthetic",
                        "approved_model_data": True,
                        "stream_output": False,
                    },
                    "secret_action": "replace",
                    "secret": "synthetic-model-key",
                },
            )
            assert response.status_code == 200
            state = response.json()["data"]
            command = {"version": state["version"], "revision": state["draft_revision"]}
            response = await client.post(path + "/test", json={**command, "acknowledge_cost": True})
            assert response.status_code == 200 and response.json()["data"]["passed"]
            response = await client.post(path + "/apply", json=command)
            assert response.status_code == 200 and response.json()["data"]["ready"]
            state = response.json()["data"]
            response = await client.post(
                "/api/v2/agent/runs",
                json={"prompt": "合成只读容器验收", "request_key": uuid4().hex},
            )
            assert response.status_code == 202
            identifier = response.json()["data"]["id"]
            async with asyncio.timeout(20):
                while True:
                    task = (await client.get("/api/v2/agent/runs/" + identifier)).json()["data"]
                    if task["status"] not in {"running", "queued"}:
                        break
                    await asyncio.sleep(0.02)
            assert task["status"] == "completed"
            await app._agent.close()
            app._agent = AgentCoordinator(app)
            state = (await client.get(path)).json()["data"]
            assert (
                state["ready"]
                and state["source"] == "managed"
                and state["last_test"]["result"]["passed"]
            )
            assert (
                await client.post(path + "/disable", json={"version": state["version"]})
            ).status_code == 200
            await app._agent.close()
            app._agent = AgentCoordinator(app)
            state = (await client.get(path)).json()["data"]
            assert state["source"] == "disabled" and not state["ready"]
            assert (await client.get("/api/v2/projects")).status_code == 200
        print("CONTAINER_AGENT_OK http synthetic-provider save test apply task restart disable")
    finally:
        if app._agent:
            await app._agent.close()
        await app.verifier.aclose()
        await app.sessions.kw["bind"].dispose()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    asyncio.run(main())
