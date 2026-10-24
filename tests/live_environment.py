"""真实验收仅允许显式本机入口，不探测或接管其他项目端口。"""

import os
from urllib.parse import urlsplit

SERVICE_URL = os.environ.get("OFFICE_TEST_URL", "http://127.0.0.1:8800").rstrip("/")
parsed = urlsplit(SERVICE_URL)
if (
    parsed.scheme != "http"
    or parsed.hostname != "127.0.0.1"
    or not parsed.port
    or parsed.path
    or parsed.query
    or parsed.fragment
    or parsed.username is not None
    or parsed.password is not None
):
    raise RuntimeError("OFFICE_TEST_URL仅接受带端口的本机HTTP根地址")
