"""服务器拥有任务和审批状态；可选模型配置不改变原数据业务授权。"""

import asyncio
import hashlib
import json
from importlib import import_module
from importlib.util import find_spec

from app.application.agent_runner import AgentRunner, recover_runs
from app.application.contracts import validate_output
from app.common.errors import DomainError, require
from app.modules.access.service import Access
from app.modules.agent.service import AgentRecords, run_view


class AgentCoordinator:
    def __init__(self, application, *, model_context=None):
        self.application, self.settings = application, application.settings
        self.sessions = application.sessions
        self.model_context = model_context
        self.runner: AgentRunner | None = None
        self.config = None
        self.lock = asyncio.Lock()
        self.started = False
        self.closed = False
        self.error = ""

    async def start(self):
        async with self.lock:
            if self.started:
                return
            try:
                await recover_runs(self.sessions)
                if self.settings.agent_config_file is not None:
                    model = import_module("office_agent.config").AgentConfig
                    config = await asyncio.to_thread(
                        model.load, str(self.settings.agent_config_file)
                    )
                    self.config = config
                    require(
                        config.service_url == self.settings.public_url,
                        503,
                        "execution_unavailable",
                        "Agent配置的企业地址与当前实例不一致",
                    )
                    if self.model_context is None:
                        await asyncio.to_thread(
                            import_module("office_agent.providers").model_key, config
                        )
                    self.runner = AgentRunner(
                        self.application, self.config, model_context=self.model_context
                    )
            except Exception:  # noqa: BLE001 -- 配置或依赖错误不能泄漏秘密或阻断原数据功能。
                self.error = "智能助手运行依赖或配置尚未就绪，请管理员核对后重启"
            self.started = True

    async def request(
        self, principal, operation, payload, trace_id, *, presenter=None, authenticate=None
    ):
        await self.start()
        created = False
        identifier = None
        async with self.sessions.begin() as db:
            # 创建容量与请求幂等检查由同一已有权限锁串行，不在事务中执行模型。
            access = await Access.create(
                db, principal, trace_id, exclusive=operation == "agent.start"
            )
            if operation == "agent.capabilities":
                result = {
                    "installed": find_spec("office_agent") is not None,
                    "configured": self.settings.agent_config_file is not None,
                    "ready": self.runner is not None and not self.closed,
                    "message": self.error,
                }
            else:
                records = AgentRecords(access)
                if operation == "agent.start":
                    require(
                        self.runner is not None and not self.closed and authenticate is not None,
                        503,
                        "execution_unavailable",
                        self.error or "智能助手尚未配置或正在停止",
                    )
                    assert self.config is not None
                    prompt = import_module("office_agent.policy").sanitize(payload["prompt"])
                    require(
                        len(prompt.encode()) <= self.config.limits.input_bytes,
                        422,
                        "invalid_input",
                        "任务文本超出当前输入预算",
                    )
                    digest = hashlib.sha256(
                        json.dumps(
                            {"prompt": prompt, "config": self.config.model_dump(mode="json")},
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()
                    row, created = await records.create(
                        prompt,
                        payload["request_key"],
                        digest,
                        per_user=self.settings.agent_max_per_user,
                        total=self.settings.agent_max_running,
                    )
                    result, identifier = run_view(row), row.id
                elif operation == "agent.list":
                    result = await records.listing(**payload)
                elif operation == "agent.get":
                    result = run_view(await records.owned(payload["identifier"]))
                elif operation == "agent.decide":
                    result = await records.decide(**payload)
                elif operation == "agent.cancel":
                    result = await records.cancel(**payload)
                    identifier = payload["identifier"]
                else:
                    raise DomainError(422, "unsupported_operation", "未提供此助手操作")
            checked = validate_output(operation, payload, result)
            response = (
                presenter(checked, replayed=operation == "agent.start" and not created)
                if presenter
                else checked
            )
        if created and self.runner is not None:
            # DB受理已提交后才启动模型，重复同一request_key只回读原任务。
            self.runner.launch(identifier, authenticate)
        elif operation == "agent.cancel" and self.runner is not None:
            self.runner.cancel(identifier)
        return response

    async def close(self):
        self.closed = True
        if self.runner is not None:
            await self.runner.close()
