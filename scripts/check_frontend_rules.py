"""核对模板与同目录脚本是否符合可机检的前端规则，不启动浏览器、不连接实例。

`AGENTS.md` 与 `docs/UI_DESIGN_GUIDE.md` 里的若干硬性约束此前只靠人工遵守：
任何回归都不会因为违规而失败。本脚本把其中**不依赖渲染**的部分变成门禁。

检查项：
- `inline_style`：模板里的 `style="..."` 内联样式（应改为样式表里的类）
- `inline_handler`：`onclick=`／`onchange=` 等内联事件属性（应绑定在脚本里）
- `inner_html`：脚本中的 `innerHTML`／`outerHTML`／`insertAdjacentHTML`／`document.write`
  （Markdown 与模型输出必须走允许列表 DOM 构建）
- `native_dialog`：脚本中的 `window.alert`／`confirm`／`prompt`（应用共享确认组件）
- `breakpoint`：`@media` 里出现的视口宽度必须落在既定的四档刻度内
- `media_spacing`：`@media` 与括号之间应有一个空格，避免同一写法混用

规则命中数与 `tests/expected/frontend-rules-baseline.json` 逐项比对：
数量增加即失败；数量减少也要求同步收紧基线，防止回退。这与
`scripts/check_ui_styles.py` 使用同一套双向锁定策略。
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "office-data-service/app/entrypoints/web/templates"
BASELINE = ROOT / "tests/expected/frontend-rules-baseline.json"

# 断点冻结刻度：当前样式表实际使用且各有明确用途的视口宽度。
# 540 窄屏描述列表 / 600 设置页折行 / 650 筛选与弹窗折行 / 700 主断点（701 为其补集）
# / 760 工作区抽屉转全宽 / 1000 侧栏收起 / 1100 分页区间隐藏 / 1200 助手并排。
#
# 这里冻结而不是收敛：760 与 700 之间若合并，会让 700–760px 区间的 `.workspace`
# 从 `min(840px,95vw)` 变成 100vw（实测约 36px 差异）；650、600 归并同样改变
# 筛选与弹窗的折行时机。现有浏览器回归只覆盖 1440/1024/768/390/320，
# 改完无法证伪，因此本轮只阻止**新增**档位，归并留给带视觉取证的后续工作。
BREAKPOINTS = (540, 600, 650, 700, 701, 760, 1000, 1100, 1200)

INLINE_STYLE = re.compile(r'\sstyle="[^"]*"')
INLINE_HANDLER = re.compile(r"\son(?:click|change|submit|input|load|focus|blur|keydown|keyup)\s*=")
INNER_HTML = re.compile(
    r"\.(?:innerHTML|outerHTML)\s*=|\binsertAdjacentHTML\s*\(|\bdocument\.write(?:ln)?\s*\("
)
# 只认真正的原生对话框：加 window. 前缀的 alert/confirm/prompt，以及未加前缀的
# alert/prompt。本项目在 interactions.js 里自己定义了共享的 confirm(message, options)，
# 裸 confirm(...) 是那个组件而不是浏览器原生弹窗，不能误判。
NATIVE_DIALOG = re.compile(
    r"\bwindow\.(?:alert|confirm|prompt)\s*\(|(?<![\w.])(?:alert|prompt)\s*\("
)
MEDIA_QUERY = re.compile(r"@media\s*(\([^{]*)\{")
MEDIA_WIDTH = re.compile(r"\((?:max|min)-width\s*:\s*(\d+)px\)")
MEDIA_SPACING = re.compile(r"@media\(")

COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# Jinja 注释不参与检查，避免把说明文字里的示例当成代码。
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def templates_and_scripts():
    """返回 [(相对名, 文本)]：全部模板与同目录脚本。"""
    rows = []
    for path in sorted(TEMPLATES.iterdir()):
        if path.suffix in {".html", ".js", ".css"}:
            rows.append((path.name, path.read_text(encoding="utf-8")))
    assert rows, f"模板目录为空：{TEMPLATES}"
    return rows


def script_files(rows):
    return [(name, text) for name, text in rows if name.endswith(".js")]


def style_files(rows):
    return [(name, text) for name, text in rows if name.endswith(".css")]


def html_files(rows):
    return [(name, text) for name, text in rows if name.endswith(".html")]


def strip_non_code(text):
    return JINJA_COMMENT.sub("", text)


def count_matches(rows, pattern, skip_comments=False):
    total = 0
    for _name, text in rows:
        body = strip_non_code(text)
        if skip_comments:
            body = COMMENT.sub("", body)
        total += len(pattern.findall(body))
    return total


def off_scale_breakpoints(rows):
    hits = []
    for name, text in style_files(rows):
        plain = COMMENT.sub("", text)
        for match in MEDIA_QUERY.finditer(plain):
            for width in MEDIA_WIDTH.findall(match.group(1)):
                if int(width) not in BREAKPOINTS:
                    hits.append((name, int(width)))
    return hits


def measure():
    """返回 {规则: 数量}；断点越界单独成行便于阅读。"""
    rows = templates_and_scripts()
    scripts = script_files(rows)
    html = html_files(rows)
    styles = style_files(rows)
    return {
        "inline_style": count_matches(html, INLINE_STYLE),
        "inline_handler": count_matches(html, INLINE_HANDLER),
        "inner_html": count_matches(scripts, INNER_HTML, skip_comments=True),
        "native_dialog": count_matches(scripts, NATIVE_DIALOG, skip_comments=True),
        "media_spacing": count_matches(styles, MEDIA_SPACING, skip_comments=True),
        "breakpoint": len(off_scale_breakpoints(rows)),
    }


def load_baseline():
    if not BASELINE.exists():
        return None
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def compare(actual, baseline):
    problems = []
    expected = baseline["rules"]
    for rule in sorted(set(actual) | set(expected)):
        want = expected.get(rule)
        got = actual.get(rule)
        if want is None:
            problems.append(f"{rule} 未登记，请更新基线")
        elif got is None:
            problems.append(f"基线仍登记已不存在的规则 {rule}，请更新基线")
        elif got > want:
            problems.append(f"{rule} 由 {want} 增加到 {got}：新增了违规")
        elif got < want:
            problems.append(f"{rule} 由 {want} 减少到 {got}：请更新基线以收紧")
    return problems


def write_baseline(actual):
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "note": (
            "前端规则存量基线。数量增加即失败，减少也必须同步收紧；"
            "更新命令：uv run --frozen python scripts/check_frontend_rules.py --update"
        ),
        "rules": actual,
    }
    BASELINE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def check():
    """返回实际计数；有新增违规或未收紧时抛 AssertionError。"""
    rows = templates_and_scripts()
    actual = measure()
    baseline = load_baseline()
    if baseline is None:
        raise AssertionError(
            "缺少前端规则基线 tests/expected/frontend-rules-baseline.json；"
            "首次启用请运行 scripts/check_frontend_rules.py --update"
        )
    problems = compare(actual, baseline)
    detail = off_scale_breakpoints(rows)
    if detail:
        problems.extend(f"{name} 使用了未登记断点 {width}px" for name, width in detail)
    assert not problems, "前端规则不符：\n  " + "\n  ".join(problems)
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="按当前模板重写基线")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出当前计数")
    arguments = parser.parse_args()
    if arguments.update:
        actual = measure()
        write_baseline(actual)
        print(
            "FRONTEND_RULES_BASELINE_UPDATED",
            json.dumps(actual, ensure_ascii=False, sort_keys=True),
        )
        return
    if arguments.json:
        print(json.dumps(measure(), ensure_ascii=False, sort_keys=True))
        return
    actual = check()
    print("FRONTEND_RULES_OK", json.dumps(actual, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
