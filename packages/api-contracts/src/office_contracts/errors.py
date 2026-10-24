"""公开异常不保存原始请求／响应；异常文本均为安全固定说明。"""


class ConfigurationError(ValueError):
    """应用注册配置无效。"""


class ProtocolError(ValueError):
    def __init__(self, message: str = "接口响应不符合已声明契约"):
        self.status: int | None = None
        self.trace_id: str | None = None
        super().__init__(message)


class ContractError(Exception):
    def __init__(self, code: int):
        self.code = code
        super().__init__("操作未按约定完成")


class RemoteError(Exception):
    def __init__(self, failure, status: int):
        self.failure, self.status = failure, status
        self.code = failure.code
        super().__init__("远端操作失败，请依据受控错误码处理")


class TransportFailure(Exception):
    def __init__(self, kind: str, write_outcome: str):
        self.kind, self.write_outcome = kind, write_outcome
        super().__init__("网络请求未完成，请核对操作结果")
