"""唯一操作覆盖目录；应用、Agent和契约检查复用，不引入服务循环依赖。"""

import json
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def transport_coverage():
    return json.loads(files("app.application").joinpath("capability_map.json").read_text())
