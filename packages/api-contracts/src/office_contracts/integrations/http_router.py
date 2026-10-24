"""仅包装显式路由，保留FastAPI依赖和公开签名。"""

import inspect
from functools import wraps
from typing import Any

from fastapi import APIRouter, Response
from starlette.concurrency import run_in_threadpool

from ..codec import PreparedSuccess, adapter, prepare_success, success_model
from ..errors import ConfigurationError, ProtocolError
from ..models import Failure, Meta
from .http_boundary import trace_context


class ContractRouter(APIRouter):
    def __init__(self, *, registry, default_errors=(), **kwargs):
        self.registry = registry
        self.default_errors = tuple(default_errors)
        super().__init__(**kwargs)

    def contract(
        self, path, *, method, data_model, errors=(), status_code=200, operation_id=None, **kwargs
    ):
        native_model = kwargs.pop("response_model", None)
        if data_model is not None and native_model is not None:
            raise ConfigurationError("业务模型重复声明")
        data_model = data_model or native_model
        if data_model is None:
            raise ConfigurationError("必须显式声明业务模型")
        adapter(success_model(data_model))
        if status_code not in {200, 201, 202, 203}:
            raise ConfigurationError("包装路由需声明可带JSON正文的成功状态")
        if "responses" in kwargs:
            raise ConfigurationError("受管响应模型与错误文档由契约统一声明")
        responses: dict[int | str, dict[str, Any]] = {
            s: {"model": Failure} for s in (400, 401, 403, 404, 405, 408, 413, 415, 422, 500)
        }
        codes = {
            400001,
            400002,
            401001,
            403001,
            404001,
            405001,
            408001,
            413001,
            415001,
            422001,
            500001,
            500002,
        }
        for code in (*self.default_errors, *errors):
            spec = self.registry.by_code(code)
            if not spec or spec.severity != "error":
                raise ConfigurationError("路由错误未登记")
            codes.add(code)
            if spec.statuses == frozenset(range(400, 500)):
                responses["4XX"] = {"model": Failure}
            elif spec.statuses == frozenset(range(500, 600)):
                responses["5XX"] = {"model": Failure}
            else:
                for status in spec.statuses:
                    responses[status] = {"model": Failure}

        def decorate(endpoint):
            signature = inspect.signature(endpoint, eval_str=True)
            if (
                any(
                    p.kind in {p.VAR_KEYWORD, p.VAR_POSITIONAL}
                    for p in signature.parameters.values()
                )
                or "_contract_response" in signature.parameters
            ):
                raise ConfigurationError("受管路由需要显式参数签名")

            @wraps(endpoint)
            async def wrapped(*args, **values):
                sub_response = values.pop("_contract_response")
                if inspect.iscoroutinefunction(endpoint) or inspect.iscoroutinefunction(
                    getattr(type(endpoint), "__call__", None)  # noqa: B004 — 检查异步调用方法，不判断可调用性
                ):
                    result = await endpoint(*args, **values)
                else:
                    result = await run_in_threadpool(endpoint, *args, **values)
                if isinstance(result, Response):
                    raise ProtocolError("包装路由不接受原始Response")
                if isinstance(result, PreparedSuccess):
                    if result.model != data_model or result.operation not in {
                        "",
                        operation_id or endpoint.__name__,
                    }:
                        raise ProtocolError("准备物与路由声明不符")
                    prepared = result
                else:
                    prepared = prepare_success(
                        result,
                        data_model,
                        Meta(trace_id=trace_context.get() or "local"),
                        registry=self.registry,
                    )
                response = Response(
                    prepared.body,
                    status_code=200 if status_code == 201 and prepared.replayed else status_code,
                    media_type="application/json",
                )
                response.raw_headers.extend(
                    (k, v)
                    for k, v in sub_response.raw_headers
                    if k.lower() not in {b"content-type", b"content-length"}
                )
                if sub_response.status_code is not None and sub_response.status_code != status_code:
                    raise ProtocolError("依赖修改了未声明成功状态")
                return response

            wrapped.__signature__ = signature.replace(  # type: ignore[attr-defined]
                parameters=[
                    *signature.parameters.values(),
                    inspect.Parameter(
                        "_contract_response", inspect.Parameter.KEYWORD_ONLY, annotation=Response
                    ),
                ],
                return_annotation=success_model(data_model),
            )
            if status_code == 201:
                responses[200] = {
                    "model": success_model(data_model),
                    "description": "幂等重放返回当前实体",
                }
            self.add_api_route(
                path,
                wrapped,
                methods=[method],
                response_model=success_model(data_model),
                status_code=status_code,
                responses=responses,
                operation_id=operation_id,
                openapi_extra={"x-business-codes": sorted(codes)},
                **kwargs,
            )
            return endpoint

        return decorate

    def get(self, path, *, data_model=None, **kwargs):
        return self.contract(path, method="GET", data_model=data_model, **kwargs)

    def post(self, path, *, data_model=None, **kwargs):
        return self.contract(path, method="POST", data_model=data_model, **kwargs)

    def put(self, path, *, data_model=None, **kwargs):
        return self.contract(path, method="PUT", data_model=data_model, **kwargs)

    def patch(self, path, *, data_model=None, **kwargs):
        return self.contract(path, method="PATCH", data_model=data_model, **kwargs)

    def delete(self, path, *, data_model=None, **kwargs):
        return self.contract(path, method="DELETE", data_model=data_model, **kwargs)
