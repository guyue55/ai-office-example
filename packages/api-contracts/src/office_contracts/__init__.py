"""稳定公共入口；可选框架不在核心导入时加载。"""

from .codec import Budget, PreparedSuccess, decode, encode, prepare_success
from .errors import ConfigurationError, ContractError, ProtocolError, RemoteError, TransportFailure
from .models import ErrorInfo, Failure, Issue, Meta, Recovery, Scope, Success, Warning
from .registry import CodeRegistry, CodeSpec, make_registry

__all__ = [
    "Budget",
    "CodeRegistry",
    "CodeSpec",
    "ConfigurationError",
    "ContractError",
    "ErrorInfo",
    "Failure",
    "Issue",
    "Meta",
    "PreparedSuccess",
    "ProtocolError",
    "Recovery",
    "RemoteError",
    "Scope",
    "Success",
    "TransportFailure",
    "Warning",
    "decode",
    "encode",
    "make_registry",
    "prepare_success",
]
