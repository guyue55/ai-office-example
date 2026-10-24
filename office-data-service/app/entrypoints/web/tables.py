"""列表查询适配：原生表单输入转换集中处理，应用层仍严格校验。"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.common.errors import DomainError


def table_query(request, fields):
    query = request.query_params
    if set(query) - set(fields) or len(query.multi_items()) != len(query):
        raise DomainError(422, "invalid_query", "查询条件未定义或重复")
    result = dict(query)
    try:
        for key, default in (("limit", "10"), ("page", "1")):
            if key not in fields:
                continue
            if key == "page" and query.get("after"):
                continue
            value = query.get(key, default)
            if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
                raise ValueError("页码或数量无效")
            result[key] = int(value)
        if "archived" in result:
            if result["archived"] not in ("true", "false"):
                raise ValueError("归档条件无效")
            result["archived"] = result["archived"] == "true"
        for key in ("since", "until"):
            if query.get(key):
                day = date.fromisoformat(query[key])
                if key == "until":
                    day += timedelta(days=1)
                result[key] = datetime.combine(day, time.min, ZoneInfo("Asia/Shanghai")).isoformat()
    except (ValueError, OverflowError):
        raise DomainError(422, "invalid_query", "请核对页码、每页数量与日期范围") from None
    return result
