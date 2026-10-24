"""有界游标读取；不承诺跨请求快照，不隐式重试或返回伪装完整的首屏。"""

import json
from copy import deepcopy
from dataclasses import dataclass

from .auth import ClientError


@dataclass(frozen=True)
class ReadCollection:
    items: list[dict]
    complete: bool
    reason: str
    pages: int
    next: str | None
    bytes_retained: int
    snapshot_consistent: bool = False


def collect_pages(fetch, filters, *, max_pages=20, max_items=1000, max_bytes=5 * 1024 * 1024):
    """只用于SDK绑定的只读列表；预算不足返回继续位置，接口错误原样抛出。"""
    for value, ceiling in ((max_pages, 100), (max_items, 10000), (max_bytes, 20 * 1024 * 1024)):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise ClientError("分页预算必须为规定范围内的正整数")
    params = deepcopy(filters)
    if "page" in params:
        raise ClientError("此辅助方法仅支持游标分页")
    limit = params.pop("limit", 50)
    cursor = params.pop("after", "")
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(cursor, str):
        raise ClientError("分页数量或游标格式无效")
    items: list[dict] = []
    size, seen = 0, {cursor}
    for page in range(1, max_pages + 1):
        page_limit = min(limit, max_items - len(items))
        response = fetch(**deepcopy(params), limit=page_limit, after=cursor)
        if not isinstance(response, dict):
            raise ClientError("分页结果格式无效")
        rows, following = response.get("items"), response.get("next")
        if (
            not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)
            or len(rows) > page_limit
            or (following is not None and (not isinstance(following, str) or not following))
            or (following is not None and (not rows or following in seen))
        ):
            raise ClientError("分页结果或游标推进无效，已停止读取")
        page_bytes = len(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if size + page_bytes > max_bytes:
            # 整页不接纳，继续游标指向本页之前，避免遗漏未接纳的数据。
            return ReadCollection(items, False, "max_bytes", page, cursor, size)
        items.extend(deepcopy(rows))
        size += page_bytes
        if following is None:
            return ReadCollection(items, True, "complete", page, None, size)
        cursor = following
        seen.add(cursor)
        if len(items) == max_items:
            return ReadCollection(items, False, "max_items", page, cursor, size)
    return ReadCollection(items, False, "max_pages", max_pages, cursor, size)
