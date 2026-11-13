"""门禁脚本自身的回归；防止扫描逻辑被改坏后静默放行。

为什么需要这层：`scripts/check_frontend_rules.py` 与 `scripts/check_ui_styles.py`
是"让规范能因回归失败"的执行器。如果它们的正则或区间计算被改错，
结果是**永远通过**——比没有门禁更危险，因为看起来有守护。

这里直接调用脚本里的纯函数，用手写样本覆盖易错点：
- Jinja 注释里的示例不能被当成违规代码
- 共享 `confirm()` 组件不能被误判成原生弹窗
- `@media` 嵌套时 `:hover` 守卫的区间计算
- 断点越界要能报出文件名与宽度
- 双向锁定：增加与减少都必须报问题
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"gate_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frontend = load("check_frontend_rules")
styles = load("check_ui_styles")


# --- 前端规则：误判与漏判 -------------------------------------------------


def test_jinja_comment_examples_are_not_counted():
    # 注释里写出违规示例是常见做法，不能因此让门禁失败。
    rows = [("page.html", '{# 不要写 <div style="margin:0"> #}\n<p>正常</p>')]
    assert frontend.count_matches(frontend.html_files(rows), frontend.INLINE_STYLE) == 0


def test_inline_style_is_counted():
    rows = [("page.html", '<div style="margin:0">x</div>')]
    assert frontend.count_matches(frontend.html_files(rows), frontend.INLINE_STYLE) == 1


def test_shared_confirm_is_not_a_native_dialog():
    # 项目自己在 interactions.js 定义并导出了 confirm(message, options)。
    shared = [("interactions.js", "function confirm(message, options = {}) {}\nconfirm('x');")]
    native = [("page.js", "window.confirm('x');")]
    assert frontend.count_matches(shared, frontend.NATIVE_DIALOG, skip_comments=True) == 0
    assert frontend.count_matches(native, frontend.NATIVE_DIALOG, skip_comments=True) == 1


def test_alert_and_prompt_are_still_native():
    rows = [("page.js", "alert('a');\nprompt('b');")]
    assert frontend.count_matches(rows, frontend.NATIVE_DIALOG, skip_comments=True) == 2


def test_inner_html_inside_block_comment_is_ignored():
    rows = [("page.js", "/* el.innerHTML = x; */\nconst a = 1;")]
    assert frontend.count_matches(rows, frontend.INNER_HTML, skip_comments=True) == 0


@pytest.mark.parametrize(
    "css,expected",
    [
        ("@media (max-width:700px) { .a { color: red } }", []),
        ("@media (max-width:880px) { .a { color: red } }", [("a.css", 880)]),
        ("/* @media (max-width:999px) {} */\n@media (min-width:1000px) { .a{} }", []),
    ],
)
def test_off_scale_breakpoints(css, expected):
    assert frontend.off_scale_breakpoints([("a.css", css)]) == expected


def test_compare_reports_both_increase_and_decrease():
    baseline = {"rules": {"inline_style": 0, "inner_html": 1}}
    assert any(
        "增加" in p for p in frontend.compare({"inline_style": 2, "inner_html": 1}, baseline)
    )
    assert any(
        "收紧" in p for p in frontend.compare({"inline_style": 0, "inner_html": 0}, baseline)
    )


def test_compare_flags_unknown_and_stale_rules():
    assert any("未登记" in p for p in frontend.compare({"new_rule": 0}, {"rules": {}}))
    assert any("已不存在" in p for p in frontend.compare({}, {"rules": {"gone": 0}}))


# --- 样式规范：区间与放行 -------------------------------------------------
# 这些函数返回命中行号列表；空列表即"无违规"。


def test_hover_outside_media_query_is_unguarded():
    assert styles.unguarded_hover(".button:hover { color: red }") == [1]


def test_hover_inside_media_query_is_guarded():
    assert styles.unguarded_hover("@media (hover: hover) { .button:hover { color: red } }") == []


def test_nested_media_query_still_guards_hover():
    css = (
        "@media (min-width: 800px) {\n"
        "  @media (hover: hover) and (prefers-reduced-motion: no-preference) {\n"
        "    .button:hover { color: red }\n"
        "  }\n"
        "}"
    )
    assert styles.unguarded_hover(css) == []


def test_hover_after_guard_block_closes_is_unguarded():
    # 守卫块结束后的 :hover 不再受保护，这是最容易算错的一种。
    css = "@media (hover: hover) { .a:hover { color: red } }\n.b:hover { color: blue }"
    assert styles.unguarded_hover(css) == [2]


def test_white_space_property_is_not_a_color_literal():
    # `white-space` 含 `white`，不能算颜色字面量。
    assert styles.color_literals("a { white-space: nowrap }") == []


@pytest.mark.parametrize(
    "css",
    [
        "a { color: #fff }",
        "a { color: rgba(0, 0, 0, 0.5) }",
        "a { color: white }",
        "a { border-color: hsl(1, 2%, 3%) }",
    ],
)
def test_color_literals_are_found(css):
    assert styles.color_literals(css) == [1], css


@pytest.mark.parametrize(
    "css,expected",
    [
        ("a { border-radius: 0 }", []),
        ("a { border-radius: 2px }", []),
        ("a { border-radius: 50% }", []),
        ("a { border-radius: var(--radius-control) }", []),
        ("a { border-radius: 8px }", [1]),
    ],
)
def test_off_scale_radius(css, expected):
    assert styles.off_scale_radius(css) == expected


def test_comment_only_violation_is_ignored():
    # 注释里写示例不能让门禁失败。
    css = "/* .a:hover { color: #fff } */\n.b { color: var(--ink) }"
    assert styles.unguarded_hover(css) == []
    assert styles.color_literals(css) == []


def test_one_px_neutral_border_is_counted():
    assert styles.thick_border("a { border: 1px solid var(--line) }") == [1]
    assert styles.thick_border("a { border-top: 1px solid var(--line) }") == [1]
