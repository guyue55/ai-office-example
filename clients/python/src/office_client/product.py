"""构建生成的公开产品配置；仅修改根template.toml后运行配置工具。"""

import json
from pathlib import Path

PRODUCT = json.loads(Path(__file__).with_name("product.json").read_text(encoding="utf-8"))
