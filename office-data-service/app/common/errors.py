"""纯业务错误类型、输入问题与安全日志；不依赖协议响应或业务码表。"""

import logging
import re

from pydantic import ValidationError


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, *, details: list | None = None):
        self.status, self.code, self.message = status, code, message
        self.details = details or []
        super().__init__(message)

    def payload(self, trace_id):
        result = {"code": self.code, "message": self.message, "trace_id": trace_id}
        if self.details:
            result["details"] = self.details
        return result


def validation_error(error):
    details = []
    issues = (
        error.errors(include_input=False, include_context=False)
        if isinstance(error, ValidationError)
        else error.errors()
    )
    for issue in issues[:20]:
        path = [
            str(part)
            if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}|[0-9]{1,6}", str(part))
            else "field"
            for part in issue.get("loc", ())[:8]
        ]
        details.append({"path": path, "code": issue["type"]})
    return DomainError(422, "invalid_input", "请求字段、类型或参数不符合接口定义", details=details)


def require(condition, status: int, code: str, message: str, *, details: list | None = None):
    if not condition:
        raise DomainError(status, code, message, details=details)


def log_rejection(trace_id, code: str, status: int, transport: str):
    """拒绝／依赖故障独立于业务事务日志，回滚后仍可追查。"""
    logging.getLogger("office.service").warning(
        "[Trace: OfficeService/rejected] trace_id=%s transport=%s status=%s code=%s",
        trace_id,
        transport,
        status,
        code,
    )
