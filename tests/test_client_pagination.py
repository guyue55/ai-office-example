"""构造只读响应验证预算、筛选、游标及错误传播，不连接真实身份。"""

from unittest.mock import Mock

import pytest
from office_client import OfficeClient
from office_client.auth import ClientError
from office_client.pagination import collect_pages


def test_collect_keeps_filters_and_uses_existing_read_methods():
    client = object.__new__(OfficeClient)
    client.records = Mock(
        side_effect=[{"items": [{"id": "1"}], "next": "1"}, {"items": [{"id": "2"}], "next": None}]
    )
    result = client.collect_records(project_id="p", limit=1)
    assert result.complete and not result.snapshot_consistent
    assert result.items == [{"id": "1"}, {"id": "2"}]
    assert client.records.call_args_list[0].kwargs == {"project_id": "p", "limit": 1, "after": ""}
    assert client.records.call_args_list[1].kwargs == {"project_id": "p", "limit": 1, "after": "1"}


@pytest.mark.parametrize(
    "budget,reason",
    [
        ({"max_pages": 1}, "max_pages"),
        ({"max_items": 1}, "max_items"),
        ({"max_bytes": 1}, "max_bytes"),
    ],
)
def test_budget_never_masquerades_as_complete(budget, reason):
    fetch = Mock(return_value={"items": [{"id": "1"}], "next": "1"})
    result = collect_pages(fetch, {}, **budget)
    assert not result.complete and result.reason == reason
    assert result.next == ("" if reason == "max_bytes" else "1")
    assert result.items == ([] if reason == "max_bytes" else [{"id": "1"}])
    fetch.assert_called_once()


def test_last_page_at_item_budget_is_complete():
    result = collect_pages(lambda **_: {"items": [{"id": "1"}], "next": None}, {}, max_items=1)
    assert result.complete


@pytest.mark.parametrize(
    "response",
    [
        {"items": [], "next": "1"},
        {"items": [{}], "next": ""},
        {"items": [{}], "next": 1},
        {"next": None},
    ],
)
def test_malformed_pages_are_rejected(response):
    with pytest.raises(ClientError):
        collect_pages(lambda **_: response, {})


def test_cursor_cycle_is_not_retried():
    fetch = Mock(return_value={"items": [{}], "next": "a"})
    with pytest.raises(ClientError, match="推进"):
        collect_pages(fetch, {})
    assert fetch.call_count == 2


def test_errors_propagate_instead_of_returning_partial_success():
    error = ClientError("撤权")
    fetch = Mock(side_effect=[{"items": [{}], "next": "a"}, error])
    with pytest.raises(ClientError) as caught:
        collect_pages(fetch, {})
    assert caught.value is error and fetch.call_count == 2


@pytest.mark.parametrize(
    "kwargs",
    [{"max_pages": True}, {"max_pages": 101}, {"max_items": 0}, {"max_bytes": 30 * 1024 * 1024}],
)
def test_budgets_are_strict(kwargs):
    fetch = Mock()
    with pytest.raises(ClientError):
        collect_pages(fetch, {}, **kwargs)
    fetch.assert_not_called()
