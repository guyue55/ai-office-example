"""候选框的展开与过滤是两件事，这里锁定这条交互约定。

回归目标（每条都对应一个真实误操作）：
- 已选中模型后重新聚焦或点展开按钮，必须看到**全部**候选，而不是被当前
  输入值过滤成只剩自己那一项。
- 只有真正在输入文本时，才按文本缩小候选。
- 展开按钮是真开关：打开态再点关闭，且判定不依赖 focus 触发的重画结果。
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "office-data-service/app/entrypoints/web/templates/editable-combobox.js"
).read_text(encoding="utf-8")


def test_draw_filters_only_when_a_query_is_given():
    assert "function draw(query)" in SOURCE
    # 过滤必须来自参数，而不是每次都读 input.value
    assert "const needle = (query === undefined ? '' : query).trim().toLowerCase();" in SOURCE
    assert "? items.filter(" in SOURCE and ": items.slice()" in SOURCE


def test_focus_and_toggle_expand_the_full_list():
    assert "input.addEventListener('focus', () => draw());" in SOURCE
    assert "draw(input.value)" in SOURCE  # 输入时才过滤


def test_toggle_decides_before_redrawing():
    # 旧写法把打开态取在 focus 之前，focus 的重画会把「打开」判成「关闭」
    assert "const open = !list.hidden; input.focus(); if (open) close(); else draw();" not in SOURCE
    assert "if (!list.hidden) { close(); return; }" in SOURCE


def test_custom_value_row_is_still_offered():
    # 自由输入的自定义模型 ID 仍要作为候选出现，不能被全量展开淹没
    assert "使用此自定义模型ID" in SOURCE


def test_no_duplicate_custom_row_when_value_matches_a_candidate():
    assert re.search(
        r"if \(input\.value && !items\.some\(item => item\.id === input\.value\)\)", SOURCE
    )
