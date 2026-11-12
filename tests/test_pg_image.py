"""隔离数据库镜像来源与部署文件的一致性；防止两处摘要各自漂移。"""

from pathlib import Path

import yaml
from pg_image import RELEASE_IMAGE

ROOT = Path(__file__).resolve().parents[1]


def test_release_image_matches_dev_compose():
    """默认镜像必须与 deploy/compose.dev.yaml 的 postgres 服务完全一致。"""
    compose = yaml.safe_load((ROOT / "deploy/compose.dev.yaml").read_text(encoding="utf-8"))
    deployed = compose["services"]["postgres"]["image"]
    assert deployed == RELEASE_IMAGE, (
        "隔离测试默认镜像与部署文件不一致，验收结论可能不适用于真实部署",
        deployed,
        RELEASE_IMAGE,
    )


def test_release_image_pins_digest():
    """默认镜像必须钉死摘要；只有显式环境变量才能在离线机器上临时替换。"""
    assert "@sha256:" in RELEASE_IMAGE, "默认镜像必须带摘要，标签漂移会悄悄改变验收结论"
