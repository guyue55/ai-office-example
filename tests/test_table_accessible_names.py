"""数据表格自身要有可访问名称，且不能与滚动区域同名。

名称只挂在滚动容器上时，表格节点在无障碍树里是无名的；但把容器名原样复制给
表格，读屏器会把同一个名字连念两遍。因此容器用「…表格区域」、表格用「…表格」。
"""

import re
from pathlib import Path

TEMPLATES = (
    Path(__file__).resolve().parents[1] / "office-data-service/app/entrypoints/web/templates"
)


def pages() -> list[Path]:
    return sorted(TEMPLATES.glob("*.html"))


def test_every_table_declares_its_own_name() -> None:
    """所有 <table> 都要自带 aria-label，因为容器名不会传到表格节点。"""
    missing = []
    for path in pages():
        text = path.read_text(encoding="utf-8")
        for tag in re.findall(r"<table\b[^>]*>", text):
            if "aria-label" not in tag and "aria-labelledby" not in tag:
                missing.append(f"{path.name}: {tag}")
    assert not missing, missing


def test_wrap_and_table_names_differ() -> None:
    """同一文件里滚动区域与表格的名称必须不同，避免重复朗读。"""
    duplicated = []
    for path in pages():
        text = path.read_text(encoding="utf-8")
        wraps = re.findall(r'<div class="table-wrap"[^>]*aria-label="([^"]*)"', text)
        tables = re.findall(r'<table\b[^>]*aria-label="([^"]*)"', text)
        for wrap in wraps:
            if wrap in tables:
                duplicated.append(f"{path.name}: {wrap}")
    assert not duplicated, duplicated


def test_region_names_end_with_the_area_suffix() -> None:
    """滚动区域统一以「区域」结尾，用来和表格的「表格」区分。"""
    offenders = []
    for path in pages():
        for label in re.findall(
            r'<div class="table-wrap"[^>]*aria-label="([^"]*)"',
            path.read_text(encoding="utf-8"),
        ):
            if not label.endswith("区域"):
                offenders.append(f"{path.name}: {label}")
    assert not offenders, offenders
