"""工具目录与真实SDK签名核对；避免新增工具成为任意HTTP/属性调用入口。"""

import inspect

from office_client.client import OfficeClient

from office_agent.catalog import READS, WRITES, describe


def test_registered_arguments_match_actual_sdk():
    for spec in [*READS.values(), *WRITES.values()]:
        signature = inspect.signature(getattr(OfficeClient, spec.method))
        accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in signature.parameters.values())
        if not accepts_kwargs:
            assert set(spec.arguments) <= set(signature.parameters), spec.method
        assert set(spec.required) <= set(spec.arguments)
    methods = {spec.method for spec in [*READS.values(), *WRITES.values()]}
    assert not methods & {"request", "login", "logout", "close", "auth_status"}


def test_all_catalog_items_have_descriptions_and_no_credentials():
    descriptions = describe()
    assert set(descriptions["read"]) == set(READS)
    assert set(descriptions["write"]) == set(WRITES)
    forbidden = {"password", "access_token", "api_key", "url", "headers", "path"}
    for group in descriptions.values():
        for item in group.values():
            assert not forbidden & set(item["arguments"])
