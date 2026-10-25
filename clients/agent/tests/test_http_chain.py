"""真实loopback HTTP协议链路。服务与模型响应由测试控制，不冒充真实PG或模型质量。"""

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
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
def local_server(api="openai-completions", anthropic_auth="bearer", failure_status=None):
    observations = {"model": [], "service": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, value, status=200):
            data = json.dumps(value).encode()
            self.send_response(status)
            if status == 307:
                self.send_header("Location", "/must-not-follow")
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
            expected = {
                "openai-completions": "/v1/chat/completions",
                "openai-responses": "/v1/responses",
                "anthropic-messages": "/v1/messages",
            }
            assert self.path == expected[api]
            if api == "anthropic-messages" and anthropic_auth == "x-api-key":
                assert self.headers["x-api-key"] == "model-sentinel"
                assert self.headers["Authorization"] is None
            else:
                assert self.headers["Authorization"] == "Bearer model-sentinel"
            assert "host-token-sentinel" not in str(self.headers)
            assert "host-header-sentinel" not in str(self.headers)
            assert "host-org-sentinel" not in str(self.headers)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observations["model"].append(body)
            assert "enterprise-sentinel" not in json.dumps(body)
            if failure_status is not None:
                self.respond(
                    {"error": {"type": "api_error", "message": "provider-secret-sentinel"}},
                    status=failure_status,
                )
                return
            if api == "openai-responses":
                assert body["store"] is False and "previous_response_id" not in body
                if len(observations["model"]) == 1:
                    output = [
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "read-1",
                            "name": "read_office",
                            "arguments": json.dumps(
                                {"operation": "projects", "arguments": {"limit": 10}}
                            ),
                            "status": "completed",
                        }
                    ]
                else:
                    assert "page2" in json.dumps(body["input"])
                    output = [
                        {
                            "type": "message",
                            "id": "msg_1",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "结果尚有下一页，本次未读取全部。",
                                    "annotations": [],
                                }
                            ],
                        }
                    ]
                self.respond(
                    {
                        "id": "resp_fixture",
                        "object": "response",
                        "created_at": 1,
                        "model": "test-model",
                        "status": "completed",
                        "output": output,
                        "usage": {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
                    }
                )
                return
            if api == "anthropic-messages":
                if len(observations["model"]) == 1:
                    content = [
                        {
                            "type": "tool_use",
                            "id": "read-1",
                            "name": "read_office",
                            "input": {"operation": "projects", "arguments": {"limit": 10}},
                        }
                    ]
                    reason = "tool_use"
                else:
                    assert "page2" in json.dumps(body["messages"])
                    content = [{"type": "text", "text": "结果尚有下一页，本次未读取全部。"}]
                    reason = "end_turn"
                self.respond(
                    {
                        "id": "msg_fixture",
                        "type": "message",
                        "role": "assistant",
                        "model": "test-model",
                        "content": content,
                        "stop_reason": reason,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 50, "output_tokens": 10},
                    }
                )
                return
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


@pytest.mark.parametrize(
    "api,auth",
    [
        ("openai-completions", "bearer"),
        ("openai-responses", "bearer"),
        ("anthropic-messages", "bearer"),
        ("anthropic-messages", "x-api-key"),
    ],
)
def test_model_http_to_real_sdk_contract_and_back(monkeypatch, api, auth):
    monkeypatch.setenv("AGENT_TEST_MODEL_KEY", "model-sentinel")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "host-token-sentinel")
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS",
        "X-Host-Private: host-header-sentinel\nAuthorization: Bearer host-token-sentinel",
    )
    monkeypatch.setenv("OPENAI_ORG_ID", "host-org-sentinel")
    with local_server(api, auth) as (url, observed):
        config = AgentConfig(
            service_url=url,
            model_url=url if api == "anthropic-messages" else url + "/v1",
            api=api,
            anthropic_auth=auth,
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
        assert result.usage["input_tokens"] == 100
        assert result.usage["output_tokens"] == 20
        assert result.receipts[0]["result"]["next"] == "page2"
        assert observed["service"] == [
            "/api/v2/me",
            "/api/v2/me",
            "/api/v2/me",
            "/api/v2/projects?limit=10",
            "/api/v2/me",
        ]
        assert "enterprise-sentinel" not in json.dumps(result.as_dict())


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
@pytest.mark.parametrize("status", [307, 401, 429, 503])
def test_provider_failure_does_not_retry_switch_protocol_or_echo_body(monkeypatch, api, status):
    monkeypatch.setenv("AGENT_TEST_MODEL_KEY", "model-sentinel")
    with local_server(api, failure_status=status) as (url, observed):
        config = AgentConfig(
            service_url=url,
            model_url=url if api == "anthropic-messages" else url + "/v1",
            api=api,
            model="test-model",
            api_key_env="AGENT_TEST_MODEL_KEY",
            approved_model_data=True,
        )

        async def scenario():
            async with configured_model(config) as model:
                return await run_task(model, lambda: OfficeClient(url, store=Store()), "读取项目")

        result = asyncio.run(scenario())
        assert result.status == "failed" and not result.receipts
        assert f"HTTP {status}" in result.explanation
        assert len(observed["model"]) == 1
        assert "provider-secret-sentinel" not in json.dumps(result.as_dict())
