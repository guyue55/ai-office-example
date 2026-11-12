"""助手面板的视觉语言必须走设计令牌，而不是各写一套字面量。

历史上侧栏、输入卡、气泡、浮层各用一套 6/7/8/10px 圆角与硬边竖线，
读起来像几个拼起来的部件。这里锁住「用令牌 + 投影分层」的几条约定，
避免以后又被逐处字面量改回去。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "office-data-service/app/entrypoints/web/templates"
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# 面板内允许出现颜色字面量的位置：无（全部走令牌）。
ASSISTANT = TEMPLATES / "assistant.css"
TOKENS = TEMPLATES / "design-tokens.css"


def assistant():
    return COMMENT.sub("", ASSISTANT.read_text(encoding="utf-8"))


def test_panel_separates_with_shadow_not_border():
    """侧栏与工作台之间用投影分层；竖线会把侧栏读成另一块区域。"""
    css = assistant()
    panel = re.search(r"\.assistant-panel \{([^}]*)\}", css)
    assert panel is not None
    body = panel.group(1)
    assert "box-shadow:var(--shadow-panel)" in body
    assert "border-left" not in body


def test_composer_is_a_floating_card():
    """输入区是浮在工作区上的卡片：透明底 + 圆角 + 软投影，而不是通栏白条。"""
    css = assistant()
    composer = re.search(r"\.assistant-composer \{([^}]*)\}", css)
    assert composer is not None
    assert "background:transparent" in composer.group(1)
    box = re.search(r"\.assistant-input-box \{([^}]*)\}", css)
    assert box is not None
    body = box.group(1)
    assert "border-radius:var(--radius-panel)" in body
    assert "box-shadow:var(--shadow-float)" in body


def test_send_button_is_pill():
    """发送按钮与卡片同一套圆角语言。"""
    css = assistant()
    rule = re.search(r"\.assistant-compose-actions button \{([^}]*)\}", css)
    assert rule is not None
    assert "border-radius:var(--radius-chip)" in rule.group(1)


def test_message_area_recedes_behind_the_card():
    """消息区退成浅底，输入卡才浮得起来。"""
    css = assistant()
    area = re.search(r"\.assistant-message-area \{([^}]*)\}", css)
    assert area is not None
    assert "background:var(--surface-subtle)" in area.group(1)


def test_floating_panels_share_the_panel_radius():
    """历史会话与更多操作浮层必须与面板同一档圆角与投影。"""
    css = (TEMPLATES / "styles.css").read_text(encoding="utf-8")
    rule = re.search(r"\.ui-floating-panel \{([^}]*)\}", css)
    assert rule is not None
    body = rule.group(1)
    assert "border-radius:var(--radius-panel)" in body
    assert "box-shadow:var(--shadow-panel)" in body
    assert "border:0.5px solid var(--line)" in body


def test_new_tokens_are_declared_once():
    """圆角/投影令牌只在令牌层定义，功能表只引用。"""
    tokens = TOKENS.read_text(encoding="utf-8")
    for name in ("--radius-panel", "--radius-bubble", "--radius-chip", "--shadow-float"):
        assert f"{name}:" in tokens, name
    for sheet in ("assistant.css", "styles.css"):
        text = COMMENT.sub("", (TEMPLATES / sheet).read_text(encoding="utf-8"))
        for name in ("--radius-panel", "--shadow-panel", "--shadow-float"):
            assert f"{name}:" not in text, f"{sheet} 不应重复定义 {name}"
