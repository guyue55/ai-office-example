"""真实loopback HTTP协议链路。服务与模型响应由测试控制，不冒充真实PG或模型质量。"""

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from office_client import OfficeClient
from office_contracts import Meta, Success

from office_agent.config import AgentConfig
from office_agent.providers import configured_model
from office_agent.runtime import run_task


class Store:
    def load(self):
        return {"access_token": "enterprise-sentinel", "expires_at": time.time() + 3600}

    def save(self, value):
        raise AssertionError("此测试不得保存或续期凭证")

    def clear(self):
        raise AssertionError("此测试不得注销身份")


@contextmanager
def local_server():
    observations = {"model": [], "service": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            observations["service"].append(self.path)
            assert self.headers["Authorization"] == "Bearer enterprise-sentinel"
            if self.path == "/api/v2/me":
                value = {
                    "id": "test-employee",
                    "access_revision": 1,
                    "permissions": ["project.read"],
                }
            else:
                assert self.path == "/api/v2/projects?limit=10"
                value = {"items": [{"id": "p1", "name": "测试项目"}], "next": "page2"}
            self.respond(
                Success[dict](data=value, meta=Meta(trace_id="loopback-test")).model_dump(
                    mode="json"
                )
            )

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer model-sentinel"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observations["model"].append(body)
            assert "enterprise-sentinel" not in json.dumps(body)
            if len(observations["model"]) == 1:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "read-1",
                            "type": "function",
                            "function": {
                                "name": "read_office",
                                "arguments": json.dumps(
                                    {"operation": "projects", "arguments": {"limit": 10}}
                                ),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                assert any(
                    m["role"] == "tool" and "page2" in m["content"] for m in body["messages"]
                )
                message = {"role": "assistant", "content": "结果尚有下一页，本次未读取全部。"}
                finish = "stop"
            self.respond(
                {
                    "id": "synthetic",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", observations
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_model_http_to_real_sdk_contract_and_back(monkeypatch):
    monkeypatch.setenv("AGENT_TEST_MODEL_KEY", "model-sentinel")
    with local_server() as (url, observed):
        config = AgentConfig(
            service_url=url,
            model_url=url + "/v1",
            model="test-model",
            api_key_env="AGENT_TEST_MODEL_KEY",
            approved_model_data=True,
        )

        async def scenario():
            async with configured_model(config) as model:
                return await run_task(model, lambda: OfficeClient(url, store=Store()), "列出项目")

        result = asyncio.run(scenario())
        assert result.status == "completed", result.explanation
        assert result.usage["requests"] == 2
        assert result.receipts[0]["result"]["next"] == "page2"
        assert observed["service"] == [
            "/api/v2/me",
            "/api/v2/me",
            "/api/v2/me",
            "/api/v2/projects?limit=10",
            "/api/v2/me",
        ]
        assert "enterprise-sentinel" not in json.dumps(result.as_dict())
