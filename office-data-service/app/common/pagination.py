"""行数与字节共同分页；游标仅指向实际返回的最后一项。"""

import json
from datetime import date, datetime

from app.common.errors import DomainError

PAGE_BYTES = 1536 * 1024  # 为2MiB协议外壳及元数据保留余量；大于单条1MiB提交预算。


def _json_date(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError("非JSON字段不能用于分页")


class _PageBudget:
    def __init__(self, limit):
        self.limit, self.used = limit, 0
        self.items: list[dict] = []
        self.last = None

    def append(self, row, view, key):
        if len(self.items) >= self.limit:
            return False
        item = view(row)
        size = len(json.dumps(item, ensure_ascii=False, default=_json_date).encode())
        if self.used + size > PAGE_BYTES:
            if not self.items:
                raise DomainError(413, "result_too_large", "单条结果超过读取预算，请联系管理员")
            return False
        self.items.append(item)
        self.used += size
        self.last = key(row)
        return True

    def output(self, more=False):
        return {"items": self.items, "next": str(self.last) if more else None}


def bounded_page(rows, view, key, limit):
    budget = _PageBudget(limit)
    for row in rows:
        if not budget.append(row, view, key):
            return budget.output(more=True)
    return budget.output()


async def bounded_stream_page(rows, view, key, limit):
    """最多检查一条超额记录；游标由实际返回内容决定，不跳过预算外记录。"""
    budget = _PageBudget(limit)
    async for row in rows:
        if not budget.append(row, view, key):
            return budget.output(more=True)
    return budget.output()
