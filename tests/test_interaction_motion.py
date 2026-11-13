"""交互动效与圆角刻度的一致性回归。

为什么需要这层：这些规则散布在 5 个样式表里，靠人工检查极易在后续改动中丢失，
而丢失的表现是「界面又变回生硬」——不会有报错，只会被使用者感知。
本文件锁住三类容易退化的前提：

1. 计时与缓动只能来自令牌，避免各处继续冒出 120ms/140ms/160ms/180ms 混用。
2. 可交互控件必须有按压反馈；弹窗关闭要能过渡，不能瞬间消失。
3. 圆角只用共享刻度，不允许再写字面量。

只做源码级断言，不启动浏览器：几何与动效的实际观感由
`tests/test_ui_state_browser.py` / `tests/test_ui_catalog_browser.py` 覆盖。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "office-data-service/app/entrypoints/web/templates"

# `--motion-*` 与 `--radius-*` 以外的时长/圆角写法不再允许出现在功能样式表里。
FUNCTIONAL_STYLES = (
    "styles.css",
    "shell.css",
    "assistant.css",
    "settings.css",
    "page-patterns.css",
)


def read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def strip_comments(css):
    """注释里的示例写法不参与判定，否则文档会被当成违规。"""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


@pytest.mark.parametrize("name", FUNCTIONAL_STYLES)
def test_motion_durations_come_from_tokens(name):
    css = strip_comments(read(name))
    # `transition:...120ms` / `animation:...0.18s` 这类硬编码时长会绕过统一节奏。
    durations = re.findall(r"\b\d+(?:\.\d+)?m?s\b", css)
    # 0s 与 !important 的 none 是合法用法（禁用动效），其余应改用 var(--motion-*)。
    offenders = [value for value in durations if value not in ("0s", "0ms")]
    assert offenders == [], (name, offenders)


@pytest.mark.parametrize("name", FUNCTIONAL_STYLES)
def test_radius_uses_shared_scale(name):
    css = strip_comments(read(name))
    literal = re.findall(r"border-radius:\s*([^;}]+)", css)
    offenders = []
    for value in literal:
        for part in value.split():
            # 0、百分比、999px 类全圆与 var(--radius-*) 是允许的；其余像素值必须走令牌。
            if re.fullmatch(r"0", part) or part.endswith("%") or part.startswith("var(--radius-"):
                continue
            if re.fullmatch(r"\d+px", part):
                offenders.append(part)
    assert offenders == [], (name, offenders)


def test_interactive_controls_have_press_feedback():
    css = strip_comments(read("styles.css"))
    # 按钮与链接按钮是主要点击目标，缺 `:active` 时只有颜色渐变，读起来是"按了没反应"。
    assert "button:active:not(:disabled)" in css
    assert ".button:active" in css
    # 图标按钮不做位移，但必须有底色反馈，且显式取消位移避免整行抖动。
    assert ".ui-icon-button:active:not(:disabled)" in css
    assert "transform:none" in css


def test_dialogs_have_exit_transition():
    css = strip_comments(read("styles.css"))
    # 原生 dialog 关闭先移除 open，只能靠 allow-discrete 延后隐藏，否则面板瞬间消失。
    assert "transition-behavior" not in css or "allow-discrete" in css
    assert "display var(--panel-exit) allow-discrete" in css
    assert "overlay var(--panel-exit) allow-discrete" in css
    # 关闭态必须同时清掉位移与不透明度，否则会停在动画起点而不是终点。
    assert ".workspace:not([open]) {transform:translateX(24px);opacity:0}" in css
    # 遮罩要跟着淡出，否则面板滑走而灰罩瞬没。
    assert ".workspace[open]::backdrop" in css and "opacity:1" in css


def test_page_transitions_use_native_view_transition():
    css = strip_comments(read("styles.css"))
    # 整页导航不引入 JS 拦截，靠浏览器原生能力，跨源回跳自动不参与。
    assert "@view-transition {navigation:auto}" in css
    assert "::view-transition-old(root)" in css
    assert "::view-transition-new(root)" in css
    # 侧栏与位置栏命名后保持不动，只有内容区过渡。
    assert "view-transition-name:shell-nav" in css
    assert "view-transition-name:shell-context" in css


def test_reduced_motion_disables_new_motion():
    css = strip_comments(read("styles.css"))
    # 该文件有多个 reduced-motion 分支（输入框、面板、提示等各管一段），要把它们合起来看。
    blocks = re.findall(r"@media \(prefers-reduced-motion:reduce\) \{(.*?)\n\}", css, re.DOTALL)
    assert blocks, "缺少减少动态偏好分支"
    body = "\n".join(blocks)
    # 页面切换与面板开关是本轮新增的动态，必须同样尊重系统偏好。
    assert "::view-transition-old(root),::view-transition-new(root) {animation:none}" in body
    assert ".workspace:not([open])" in body


def test_launch_button_reserves_content_space():
    """浮动按钮会盖住内容，主区必须为它留出高度，否则滚到底部仍有控件被压住。"""
    css = strip_comments(read("assistant.css"))
    match = re.search(r"main\.assistant-launch-main \{padding-bottom:calc\((\d+)px", css)
    assert match, "主区未为浮动按钮预留底部空间"
    assert int(match.group(1)) >= 96
