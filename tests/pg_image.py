"""隔离数据库测试使用的镜像来源。

发布校验必须使用固定 digest 的镜像，避免上游标签漂移悄悄改变验收结论，因此默认值始终是钉死的摘要。
离线或网络受限的开发机上，可用 OFFICE_TEST_PG_IMAGE 显式指向本机已有镜像来跑同一套用例；这种运行只是
补充证据，不能替代发布前用默认镜像得到的结论。
"""

import os

# 与 deploy/compose.dev.yaml 的 postgres 服务保持同一镜像与摘要。
RELEASE_IMAGE = (
    "pgvector/pgvector:0.8.7-pg18-trixie"
    "@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0"
)


def image() -> str:
    """返回本轮要拉起的数据库镜像；环境变量覆盖仅用于本机离线验证。"""
    override = os.environ.get("OFFICE_TEST_PG_IMAGE", "").strip()
    return override or RELEASE_IMAGE
