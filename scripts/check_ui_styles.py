"""核对工作台样式表是否符合可机检的设计规范，不连接实例或读取凭证。

对应 DSH 的做法：把设计规范写成可执行的 spec，而不是只写在文档里靠人工遵守。
本轮先落地四条不依赖渲染的规则；基线文件允许存量违规，但只要数量增加即失败。
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "office-data-service/app/entrypoints/web/templates"
BASELINE = ROOT / "tests/expected/ui-style-baseline.json"

# design-tokens.css 是唯一允许写颜色字面量的文件，其余为功能样式表。
TOKEN_FILE = "design-tokens.css"
FUNCTION_FILES = (
    "styles.css",
    "shell.css",
    "assistant.css",
    "settings.css",
    "page-patterns.css",
)

# 颜色字面量：十六进制、颜色函数，以及 white／black 关键字。
# white-space、blacklist 这类属性名与标识符不算颜色，故用词边界排除。
COLOR_LITERAL = re.compile(
    r"#(?:[0-9a-fA-F]{3,8})\b"
    r"|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|light-dark)\s*\("
    r"|(?<![\w-])(?:white|black)(?![\w-])"
)
HOVER_GUARD = re.compile(r"@media\s*\(hover\s*:\s*hover\)(?:\s+and\s+[^{}]+)?\s*\{")
HOVER_RULE = re.compile(r"[^{}]*:hover[^{}]*\{")
RADIUS = re.compile(r"border(?:-[a-z]+)?-radius\s*:\s*([^;}]+)")
NEUTRAL_BORDER = re.compile(
    r"(?<![\w-])border(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?"
    r"\s*:\s*1px\s+solid"
)
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# 共享圆角刻度。落在 0 与 4px 之间的小细节、以及 50%／999px 全圆不算未 token 化。
RADIUS_FLOOR = 4
RADIUS_CEILING = 999


def strip_comments(css):
    return COMMENT.sub("", css)


def line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def guarded_spans(css):
    spans = []
    for match in HOVER_GUARD.finditer(css):
        start = css.index("{", match.start()) + 1
        depth, index = 1, start
        while index < len(css) and depth:
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        spans.append((start, index))
    return spans


def unguarded_hover(css):
    plain = strip_comments(css)
    spans = guarded_spans(plain)
    rows = []
    for match in HOVER_RULE.finditer(plain):
        whole = match.group(0)
        selector = whole.lstrip().rstrip("{").strip()
        # `@media (hover:hover)` 的条件本身就含 `:hover`，它不是样式规则；
        # 把它算进来会凭空记一笔违规，也会掩盖「该文件真的没有 hover 规则」。
        if selector.startswith("@"):
            continue
        # 边界取闭区间：规则紧跟在 `{` 后同一行时匹配起点正好等于区间起点
        # （`[^{}]*` 允许零个字符），开区间会把合规写法误报成违规。
        if any(start <= match.start() < end for start, end in spans):
            continue
        # 行号指向 `:hover` 本身，而不是它前面那段空白。
        rows.append(line_of(plain, match.start() + whole.index(":hover")))
    return rows


def color_literals(css):
    plain = strip_comments(css)
    return [line_of(plain, match.start()) for match in COLOR_LITERAL.finditer(plain)]


def off_scale_radius(css):
    plain = strip_comments(css)
    rows = []
    for match in RADIUS.finditer(plain):
        value = match.group(1).strip()
        if "var(" in value:
            continue
        pixels = [float(number) for number in re.findall(r"(\d+(?:\.\d+)?)px", value)]
        if any(RADIUS_FLOOR < number < RADIUS_CEILING for number in pixels):
            rows.append(line_of(plain, match.start()))
    return rows


def thick_border(css):
    plain = strip_comments(css)
    return [line_of(plain, match.start()) for match in NEUTRAL_BORDER.finditer(plain)]


RULES = {
    "color_literal": color_literals,
    "unguarded_hover": unguarded_hover,
    "off_scale_radius": off_scale_radius,
    "border_1px": thick_border,
}


def measure():
    """返回 {文件名: {规则: 命中行号列表}}，只含功能样式表。"""
    files = {}
    for name in FUNCTION_FILES:
        path = TEMPLATES / name
        assert path.exists(), f"缺少样式表：{name}"
        css = path.read_text(encoding="utf-8")
        files[name] = {rule: scan(css) for rule, scan in RULES.items()}
    token = TEMPLATES / TOKEN_FILE
    assert token.exists(), f"缺少令牌层：{TOKEN_FILE}"
    return files


def counts(files):
    return {
        name: {rule: len(lines) for rule, lines in rules.items()} for name, rules in files.items()
    }


def load_baseline():
    if not BASELINE.exists():
        return None
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def compare(actual, baseline):
    """返回违规清单：新增、减少、文件集合变化三类。"""
    problems = []
    expected = baseline["files"]
    for name in sorted(set(actual) | set(expected)):
        if name not in expected:
            problems.append(f"基线缺少文件 {name}，请更新基线")
            continue
        if name not in actual:
            problems.append(f"基线仍登记已删除的文件 {name}，请更新基线")
            continue
        for rule in sorted(RULES):
            want = expected[name].get(rule)
            got = actual[name][rule]
            if want is None:
                problems.append(f"{name}.{rule} 未登记，请更新基线")
            elif got > want:
                problems.append(f"{name}.{rule} 由 {want} 增加到 {got}：新增了违规")
            elif got < want:
                problems.append(f"{name}.{rule} 由 {want} 减少到 {got}：请更新基线以收紧")
    return problems


def write_baseline(actual):
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "note": (
            "样式规范存量基线。数量增加即失败，减少也必须同步收紧；"
            "更新命令：uv run --frozen python scripts/check_ui_styles.py --update"
        ),
        "files": actual,
    }
    BASELINE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def check():
    """返回各文件各规则的实际命中行号；违规时抛 AssertionError。"""
    files = measure()
    actual = counts(files)
    baseline = load_baseline()
    if baseline is None:
        raise AssertionError(
            "缺少样式基线 tests/expected/ui-style-baseline.json；"
            "首次启用请运行 scripts/check_ui_styles.py --update"
        )
    problems = compare(actual, baseline)
    assert not problems, "样式规范不符：\n  " + "\n  ".join(problems)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true", help="按当前样式表重写基线（收紧或确认现状）"
    )
    parser.add_argument("--json", action="store_true", help="输出逐行号明细")
    arguments = parser.parse_args()
    if arguments.update:
        actual = counts(measure())
        write_baseline(actual)
        totals = {rule: sum(row[rule] for row in actual.values()) for rule in sorted(RULES)}
        print("UI_STYLE_BASELINE_UPDATED", json.dumps(totals, ensure_ascii=False))
        return
    if arguments.json:
        # 只报告当前命中明细，不受基线是否已收紧影响：基线漂移时仍需能查看实际计数，
        # 否则「看数字」也会被断言挡住，排查时只能先改基线。
        files = measure()
        print(json.dumps(files, ensure_ascii=False, indent=2))
        return
    files = check()
    totals = {rule: sum(len(row[rule]) for row in files.values()) for rule in sorted(RULES)}
    print("UI_STYLES_OK", json.dumps(totals, ensure_ascii=False))


if __name__ == "__main__":
    main()
