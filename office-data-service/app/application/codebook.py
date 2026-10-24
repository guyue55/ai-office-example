"""企业办公业务注册配置与旧符号映射；通用库不导入本模块。"""

import json
from functools import lru_cache
from importlib.resources import files

from office_contracts import make_registry
from office_contracts.registry import specs_from_rows


def build_registry():
    aliases()  # 启动阶段编译；请求不做文件I/O。
    business = json.loads(files("app.application").joinpath("codebook.json").read_text())
    return make_registry("office-ai-office", specs_from_rows(business))


@lru_cache(maxsize=1)
def aliases():
    rows = json.loads(files("office_contracts").joinpath("core_codes.json").read_text())
    rows += json.loads(files("app.application").joinpath("codebook.json").read_text())
    result = {}
    for row in rows:
        for alias in row["legacy_aliases"]:
            result[(alias["symbol"], alias.get("status", alias.get("status_class")))] = row["code"]
    return result


def legacy_code(symbol: str, status: int) -> int:
    mapping = aliases()
    return mapping.get((symbol, status), mapping.get((symbol, f"{status // 100}xx"), 500001))
