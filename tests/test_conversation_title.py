"""会话标题的清洗与截断回归；守住两类真实缺陷不复发。

历史缺陷（本次修复前）：
1. 前端用 `prompt.slice(0, 40)` 截取首条消息，按 UTF-16 码元切割会切断 emoji 的
   代理对，服务端 Pydantic 直接拒绝，新建会话变成 422 失败。
2. 标题原样入库，ANSI 转义与方向控制符（U+202E）可伪造视觉顺序，形成欺骗性标题。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "office-data-service/app/entrypoints/web/templates"


import pytest
from app.application.commands import validate_command
from app.common.text import (
    TITLE_MAX_CHARS,
    clean_title_text,
    fallback_title,
    normalize_title,
    strip_surrogates,
    truncate_chars,
)
from app.modules.agent.schemas import ConversationCreate, ConversationRename
from pydantic import ValidationError


def test_control_and_escape_sequences_are_stripped():
    assert clean_title_text("测试\x1b[31m红色\x1b[0m反转") == "测试红色反转"
    assert clean_title_text("纯\x1b]0;title\x07文本") == "纯文本"


def test_directional_controls_cannot_fake_visual_order():
    # U+202E 会让后续文本从右往左显示；必须被剥掉，而不是原样入库。
    assert clean_title_text("\u202e反转\u202c正文") == "反转正文"
    assert "\u202e" not in normalize_title("\u202e反转")


def test_whitespace_collapses_to_single_line():
    assert clean_title_text("  第一行\n第二行\t第三行  ") == "第一行 第二行 第三行"


def test_surrogates_are_removed_so_utf8_encoding_succeeds():
    # JS slice 切断代理对后的残留；不清掉就无法编码成 UTF-8，也无法写库。
    broken = "A\ud83d"
    assert strip_surrogates(broken) == "A"
    assert normalize_title(broken).encode("utf-8") == b"A"


def test_truncation_never_splits_a_character():
    text = "A" + "🎉" * 60
    truncated = truncate_chars(text, 10)
    assert len(truncated) == 10
    assert truncated == "A" + "🎉" * 9
    # 逐个码点完整，没有孤立代理项。
    assert truncated.encode("utf-8").decode("utf-8") == truncated


def test_truncation_respects_char_limit():
    assert len(normalize_title("会" * 150)) == TITLE_MAX_CHARS
    assert normalize_title("会" * 3) == "会会会"


def test_fallback_title_uses_prompt_and_falls_back_when_empty():
    assert fallback_title("查询本月所有项目的记录") == "查询本月所有项目的记录"
    assert fallback_title("\x1b[0m\u202e   ") == "新会话"


@pytest.mark.parametrize("title", ["\x1b[31m\x1b[0m", "\u202e", "   ", "\ud83d", ""])
def test_invisible_only_titles_are_rejected(title):
    with pytest.raises(ValidationError):
        ConversationCreate(title=title)
    with pytest.raises(ValidationError):
        ConversationRename(identifier="conversation", version=1, title=title)


def test_create_and_rename_normalize_before_storing():
    created = validate_command("agent.conversation.create", {"title": "\x1b[32m新会话\x1b[0m"})
    assert created["title"] == "新会话"
    renamed = validate_command(
        "agent.conversation.rename",
        {"identifier": "conversation", "version": 3, "title": "  改名\n完成  "},
    )
    assert renamed["title"] == "改名 完成"


def test_rename_still_requires_visible_text():
    with pytest.raises(ValidationError):
        validate_command(
            "agent.conversation.rename",
            {"identifier": "conversation", "version": 1, "title": "\x1b[0m"},
        )


def test_browser_slice_is_code_point_safe():
    """前端不得再按 UTF-16 码元截断；这里守护模板里的实现方式。"""
    source = (TEMPLATES / "assistant-shell.js").read_text(encoding="utf-8")
    assert "title:Array.from(prompt).slice(0,40).join('')" in source
    assert "title:prompt.slice(0,40)" not in source


def test_usage_is_rendered_as_structured_fields():
    """用量必须以可核对字段展示，且未返回用量的请求数不得被隐藏。"""
    view = (TEMPLATES / "assistant-view.js").read_text(encoding="utf-8")
    shell = (TEMPLATES / "assistant-shell.js").read_text(encoding="utf-8")
    # 五个字段都有中文标签，避免界面直接暴露 JSON 键名
    for label in (
        "requests:'模型请求次数'",
        "unmeasured_requests:'未返回用量的请求'",
        "tool_calls:'工具调用次数'",
        "input_tokens:'输入 Token'",
        "output_tokens:'输出 Token'",
    ):
        assert label in view
    # usage 渲染器必须导出，且区分「有未测量请求」与「全部返回」
    assert "detail,usage,outcomes" in view
    assert "次请求未返回用量；Token 统计与预算判断可能不完整" in view
    assert "本轮请求均返回了用量" in view
    # 任务详情不得再原样倾倒 JSON
    assert "view.detail('模型用量',turn.result.usage)" not in shell
    assert "view.usage(turn.result?.usage)" in shell


def _attached(title):
    """在不起数据库的前提下驱动真实的 attach() 分支。"""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.modules.agent.conversations import Conversations

    row = SimpleNamespace(title=title, version=0, archived=False, updated_at=datetime.now(UTC))
    instance = object.__new__(Conversations)

    async def ensure_idle(identifier):
        return row

    instance.ensure_idle = ensure_idle
    return instance, row


@pytest.mark.parametrize(
    "initial,prompt,expected",
    [
        # 占位标题：首条消息派生真实标题
        ("新会话", "统计本月各部门的报销金额", "统计本月各部门的报销金额"),
        # 派生时同样要清洗，不能把控制符写进库
        ("新会话", "查询\x1b[31m待认领\x1b[0m身份", "查询待认领身份"),
        # 清洗后为空：不得把标题变成空串，保留占位
        ("新会话", "\x1b[31m\x1b[0m\u202e", "新会话"),
        # 用户已改名：首条消息不得覆盖用户显式命名
        ("部门报销汇总", "统计本月各部门的报销金额", "部门报销汇总"),
        # 没有消息：保持原样
        ("新会话", "", "新会话"),
    ],
)
def test_attach_derives_title_only_while_placeholder(initial, prompt, expected):
    import asyncio

    instance, row = _attached(initial)
    asyncio.run(instance.attach("conversation-one", prompt))
    assert row.title == expected
    # 派生标题同样受字符上限约束
    assert len(row.title) <= TITLE_MAX_CHARS
