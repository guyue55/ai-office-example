"""工具自动生成创建标识，同时允许调用方持有并复用原标识。"""

from unittest.mock import Mock
from uuid import UUID

from office_client.client import OfficeClient


def test_project_preview_generates_key_without_mutating_generic_preview():
    client = object.__new__(OfficeClient)
    client.preview_data_change = Mock(side_effect=lambda change: {"change": change})
    first = client.preview_project("构造项目")
    key = first["change"]["payload"]["idempotency_key"]
    assert str(UUID(key)) == key
    replay = client.preview_project("构造项目", idempotency_key=key)
    assert replay == first
    other = client.preview_project("另一次独立创建")
    assert other["change"]["payload"]["idempotency_key"] != key
