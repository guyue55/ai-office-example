"""服务器拥有任务和审批状态；可选模型配置不改变原数据业务授权。"""

import asyncio
import hashlib
import json
from importlib import import_module
from importlib.util import find_spec

from sqlalchemy import select, update

from app.application.agent_runner import AgentRunner, recover_runs
from app.application.contracts import validate_output
from app.application.model_runtime import prepare_runtime, test_runtime
from app.common.errors import DomainError, require
from app.infrastructure.models import AgentRun, Audit, ModelConfiguration, ModelConnectionTest
from app.modules.access.service import Access
from app.modules.agent.conversations import Conversations
from app.modules.agent.model_store import ModelStore
from app.modules.agent.service import AgentRecords, run_view
from app.modules.agent.state import ACTIVE


class AgentCoordinator:
    def __init__(self, application, *, model_context=None):
        self.application, self.settings = application, application.settings
        self.sessions = application.sessions
        self.model_context = model_context
        self.runner: AgentRunner | None = None
        self.config = None
        self.lock = asyncio.Lock()
        self.test_lock = asyncio.Lock()
        self.started = False
        self.closed = False
        self.error = ""
        self.active_revision = None
        self.source = "external"

    async def start(self):
        async with self.lock:
            if self.started:
                return
            try:
                await recover_runs(self.sessions)
                async with self.sessions.begin() as db:
                    await db.execute(
                        update(ModelConnectionTest)
                        .where(ModelConnectionTest.state == "started")
                        .values(state="interrupted")
                    )
                async with self.sessions() as db:
                    state = await db.get(ModelConfiguration, 1)
                    if state is not None:
                        self.source = state.source
                        self.active_revision = state.active_revision
                        if state.source == "managed" and state.active_revision is not None:
                            from app.infrastructure.models import ModelConfigurationRevision

                            row = await db.get(ModelConfigurationRevision, state.active_revision)
                            require(row is not None, 503, "not_initialized", "生效配置已缺失")
                            self.config, self.runner = await prepare_runtime(
                                self.application, row, model_context=self.model_context
                            )
                if self.source == "external" and self.settings.agent_config_file is not None:
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
        if operation == "agent.config.test":
            require(
                not self.test_lock.locked(),
                409,
                "version_conflict",
                "已有连接测试正在进行，请等待回执",
            )
            async with self.test_lock:
                return await self._test_configuration(principal, payload, trace_id, presenter)
        if operation in {"agent.start", "agent.config.apply", "agent.config.disable"}:
            # 只串行配置切换与任务受理；查询／取消不被模型连接测试阻塞。
            async with self.lock:
                return await self._request(
                    principal,
                    operation,
                    payload,
                    trace_id,
                    presenter=presenter,
                    authenticate=authenticate,
                )
        return await self._request(
            principal, operation, payload, trace_id, presenter=presenter, authenticate=authenticate
        )

    async def _request(
        self, principal, operation, payload, trace_id, *, presenter=None, authenticate=None
    ):
        created = False
        identifier = None
        runtime_update = None
        async with self.sessions.begin() as db:
            # 创建容量与请求幂等检查由同一已有权限锁串行，不在事务中执行模型。
            access = await Access.create(
                db,
                principal,
                trace_id,
                exclusive=operation
                in {"agent.start", "agent.conversation.create", "agent.conversation.archive"},
            )
            if operation.startswith("agent.config."):
                result, runtime_update = await self._configuration(access, operation, payload)
            elif operation.startswith("agent.conversation."):
                conversations = Conversations(access)
                action = operation.rsplit(".", 1)[-1]
                if action == "create":
                    result = await conversations.create(**payload)
                elif action == "list":
                    result = await conversations.listing(**payload)
                elif action == "get":
                    result = await conversations.detail(**payload)
                elif action == "rename":
                    result = await conversations.rename(**payload)
                else:
                    result = await conversations.archive(**payload)
            elif operation == "agent.capabilities":
                result = {
                    "installed": find_spec("office_agent") is not None,
                    "configured": self.source == "managed"
                    or (self.source == "external" and self.settings.agent_config_file is not None),
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
                    persisted = await db.get(ModelConfiguration, 1)
                    require(
                        persisted is not None
                        and persisted.source == self.source
                        and persisted.active_revision == self.active_revision,
                        503,
                        "execution_unavailable",
                        "运行配置与已提交状态不一致，请重启服务恢复；未启动模型",
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
                            {
                                "prompt": prompt,
                                "config": self.config.model_dump(mode="json"),
                                "conversation_id": payload.get("conversation_id"),
                                "context_page": payload.get("context_page", ""),
                            },
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
                        conversation_id=payload.get("conversation_id"),
                        context_page=payload.get("context_page", ""),
                    )
                    if created:
                        row.model_revision = self.active_revision
                    result, identifier = run_view(row), row.id
                elif operation == "agent.list":
                    result = await records.listing(**payload)
                elif operation == "agent.get":
                    row = await records.owned(payload["identifier"])
                    result = run_view(row)
                    if (
                        row.conversation_id
                        and row.access_fingerprint != await access.authorization_fingerprint()
                    ):
                        result.update(
                            prompt="权限配置已变化，此轮内容已隐藏。",
                            pending=None,
                            result=None,
                            history_hidden=True,
                        )
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
        if runtime_update is not None:
            old = self.runner
            self.config, self.runner, self.source, self.active_revision = runtime_update
            self.error = ""
            if old is not None:
                await old.close()
        if created and self.runner is not None:
            # DB受理已提交后才启动模型，重复同一request_key只回读原任务。
            self.runner.launch(identifier, authenticate)
        elif operation == "agent.cancel" and self.runner is not None:
            self.runner.cancel(identifier)
        return response

    async def _configuration(self, access, operation, payload):
        access.need("agent.config.read")
        store = ModelStore(access, self.settings)
        state = await store.state()
        update = None
        if operation == "agent.config.save":
            await store.save(state, payload)
        elif operation in {"agent.config.apply", "agent.config.disable"}:
            access.need("agent.config.apply")
            store.expect(state, payload["version"])
            active = await access.db.scalar(
                select(AgentRun.id).where(AgentRun.status.in_(ACTIVE)).limit(1)
            )
            require(
                active is None and (self.runner is None or not self.runner.tasks),
                409,
                "version_conflict",
                "存在运行中或待确认任务，请先结束后再切换模型配置",
            )
            if operation == "agent.config.apply":
                require(
                    payload["revision"] == state.draft_revision,
                    409,
                    "version_conflict",
                    "请核对最新草稿后启用",
                )
                row = await store.revision(payload["revision"])
                config, runner = await prepare_runtime(
                    self.application, row, model_context=self.model_context
                )
                state.source, state.active_revision = "managed", row.revision
                update = (config, runner, state.source, state.active_revision)
                action = "agent.config.applied"
            else:
                state.source, state.active_revision = "disabled", None
                update = (None, None, "disabled", None)
                action = "agent.config.disabled"
            state.version += 1
            access.audit(
                action,
                "model-configuration",
                {"version": state.version, "revision": state.active_revision},
            )
        message = self.error
        if update:
            message = "配置已启用" if update[1] is not None else "助手已停用；不会回退到外部文件"
        elif not message and state.source == "external" and self.settings.agent_config_file is None:
            message = "尚无生效配置，请保存草稿并显式启用"
        elif not message and state.source == "disabled":
            message = "助手已停用；可核对草稿后重新启用"
        elif not message and state.draft_revision != state.active_revision:
            message = "草稿与当前生效修订不同；保存不会自动切换模型"
        result = await store.status(
            state,
            installed=find_spec("office_agent") is not None,
            ready=(
                update[1] is not None if update else self.runner is not None and not self.closed
            ),
            message="配置已保存，需显式启用才会生效"
            if operation == "agent.config.save"
            else message,
        )
        return result, update

    async def _test_configuration(self, principal, payload, trace_id, presenter):
        async with self.sessions.begin() as db:
            access = await Access.create(db, principal, trace_id)
            access.need("agent.config.read")
            access.need("agent.config.test")
            store = ModelStore(access, self.settings)
            state = await store.state()
            store.expect(state, payload["version"])
            require(
                payload["revision"] == state.draft_revision,
                409,
                "version_conflict",
                "请先核对最新草稿",
            )
            row = await store.revision(payload["revision"])
            config, runner = await prepare_runtime(
                self.application, row, model_context=self.model_context, synthetic=True
            )
            attempt = ModelConnectionTest(
                revision=row.revision,
                actor_id=access.actor.id,
                api=row.config["api"],
                model=row.config["model"],
                state="started",
            )
            db.add(attempt)
            await db.flush()
            attempt_id, actor_id = attempt.id, access.actor.id
            access.audit(
                "agent.config.tested",
                str(row.revision),
                {"phase": "started", "revision": row.revision},
            )
        # 供应商网络等待不占用数据库连接或事务。没有自动重试。
        try:
            outcome = await test_runtime(config, runner)
        except asyncio.CancelledError:
            async with self.sessions.begin() as db:
                attempt = await db.get(ModelConnectionTest, attempt_id, with_for_update=True)
                attempt.state = "interrupted"
                db.add(
                    Audit(
                        actor_id=actor_id,
                        action="agent.config.tested",
                        resource_id=str(payload["revision"]),
                        detail={"phase": "interrupted"},
                        trace_id=trace_id,
                    )
                )
            raise
        result = validate_output(
            "agent.config.test", payload, {"revision": payload["revision"], **outcome}
        )
        # 完成留痕不依赖发起者仍有权限；读取响应另行核验，不能撤权后丢失测试事实。
        async with self.sessions.begin() as db:
            attempt = await db.get(ModelConnectionTest, attempt_id, with_for_update=True)
            attempt.state, attempt.result = "completed", result
            db.add(
                Audit(
                    actor_id=actor_id,
                    action="agent.config.tested",
                    resource_id=str(payload["revision"]),
                    detail={
                        "phase": "completed",
                        "passed": outcome["passed"],
                        "category": outcome["category"],
                    },
                    trace_id=trace_id,
                )
            )
        async with self.sessions.begin() as db:
            access = await Access.create(db, principal, trace_id)
            access.need("agent.config.read")
            access.need("agent.config.test")
        return presenter(result, replayed=False) if presenter else result

    async def close(self):
        self.closed = True
        if self.runner is not None:
            await self.runner.close()
