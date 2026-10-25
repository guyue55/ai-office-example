"""内部Agent的异步应用端口；复用真实操作目录和每次鉴权，不转发员工令牌。"""

from importlib import import_module
from uuid import uuid4

from pydantic_core import to_jsonable_python

from app.application.capability_catalog import transport_coverage
from app.common.errors import DomainError


class ApplicationToolBridge:
    def __init__(self, application, authenticate, *, observer=None):
        self.application = application
        self.authenticate = authenticate
        self.observer = observer
        # 仅可选Agent启用后导入，普通数据服务不需要模型运行时。
        self.failure = import_module("office_agent.policy").ToolFailure
        self.operations = {
            row["sdk"]: name
            for name, row in transport_coverage().items()
            if row["mode"] == "direct" and not name.startswith("agent.")
        }

    async def call(self, method: str, **arguments):
        operation = self.operations.get(method)
        if operation is None:
            raise self.failure("未注册的内部Agent工具")
        payload = dict(arguments)
        if operation in {"schema.get", "record.export.confirmed"}:
            payload["identifier"] = payload.pop("project_id")
        elif operation in {"record.get", "record.confirmed", "record.history"}:
            payload["identifier"] = payload.pop("record_id")
        elif operation in {"record.confirm", "record.correct"}:
            payload = {"identifier": payload.pop("record_id"), "payload": payload}
        elif operation == "record.create":
            payload = {"payload": payload}
        try:
            # 不持有旧Principal、不使用超级账号；当前会话、权限和版本由原应用逐次核验。
            principal = await self.authenticate()
            if self.observer:
                await self.observer(method, "before", None)
            result = await self.application.call(principal, operation, payload, str(uuid4()))
            # 原应用返回已校验的Python字典；Decimal/日期须按JSON语义转换，不能假设是模型对象。
            result = to_jsonable_python(result)
            if self.observer:
                await self.observer(method, "after", result)
            return result
        except DomainError as error:
            if self.observer and error.status < 500:
                # 明确回滚／拒绝与丢失回执区分，不能将普通403／409误报成可能已写入。
                await self.observer(
                    method, "rejected", {"status": error.status, "code": error.code}
                )
            raise self.failure(
                f"内部工具未完成（HTTP {error.status}，原因 {error.code}）；请核对操作状态",
                outcome_unknown=error.status >= 500,
            ) from None
