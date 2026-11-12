"""单行文本清洗与按码点截断；不截断字符、不保留可制造视觉欺骗的控制符。

为什么要单独一层：标题由前端从首条消息截取，属于**未受信文本**。原实现直接
`prompt.slice(0, 40)` 再入库，带来两类真实缺陷：

1. JS 的 `slice` 按 UTF-16 码元切割，遇到代理对（emoji）会留下孤立高位代理项，
   JSON 传输后 Pydantic 直接拒绝（`string_unicode`），新建会话变成 422 失败。
2. ANSI 转义与方向控制符可原样入库：`U+202E` 能把标题视觉反转，形成欺骗性标题；
   `ESC[...]` 在终端类界面会改写显示。

因此清洗在服务端做一次权威归一：剥离 OSC/CSI/ESC 转义、C0/C1 控制符与方向
控制符，压缩空白，再按**码点**截断（绝不切断一个字符）。
"""

import re

# 终端 OSC 序列（含未终止的尾段）。
OSC_SEQUENCE = re.compile(r"(?:\x1b\]|\x9d)(?:(?!\x07|\x1b\\)[\s\S])*(?:\x07|\x1b\\|$)")
# CSI 序列，例如 SGR 颜色码。
CSI_SEQUENCE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
# 其余两字节 ESC 控制序列。
ESC_SEQUENCE = re.compile(r"\x1b[@-_]")
# 非空白 C0/C1 控制符（保留制表、换行、回车，后面统一压缩成单空格）。
CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# 方向与不可见控制符，可让显示的标题产生欺骗效果。
DIRECTIONAL_CONTROL = re.compile(
    "[\u200b\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]"
)
WHITESPACE = re.compile(r"\s+")

#: 单行文本（如会话标题）的字符上限，与对应数据库列宽保持一致。
TITLE_MAX_CHARS = 100


#: 孤立代理项（UTF-16 半字符）。按 UTF-8 编解码即丢弃，不能留在库或响应里。
SURROGATE = re.compile("[\ud800-\udfff]")


def strip_surrogates(value: str) -> str:
    """移除孤立代理项；它们无法编码成 UTF-8，会让校验与写库直接失败。"""
    return SURROGATE.sub("", value)


def clean_title_text(value: str) -> str:
    """剥离转义、控制符与方向符，压缩成单行并去首尾空白。"""
    text = OSC_SEQUENCE.sub("", strip_surrogates(value))
    text = CSI_SEQUENCE.sub("", text)
    text = ESC_SEQUENCE.sub("", text)
    text = CONTROL_CHARACTER.sub("", text)
    text = DIRECTIONAL_CONTROL.sub("", text)
    return WHITESPACE.sub(" ", text).strip()


def truncate_chars(value: str, limit: int = TITLE_MAX_CHARS) -> str:
    """按码点截断，绝不切断一个字符（代理对与组合字符都完整保留）。"""
    if limit <= 0:
        raise ValueError("limit 必须是正整数")
    return value if len(value) <= limit else value[:limit]


def normalize_title(value: str, limit: int = TITLE_MAX_CHARS) -> str:
    """归一并截断；结果可能为空，由调用方决定替代文案。"""
    return truncate_chars(clean_title_text(value), limit).rstrip()


def fallback_title(prompt: str, limit: int = TITLE_MAX_CHARS) -> str:
    """从首条消息派生确定性标题；清洗后为空时回退到通用文案。"""
    return normalize_title(prompt, limit) or "新会话"
