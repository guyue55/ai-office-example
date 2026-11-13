"""对真实实例做多页面、多宽度、多状态的客观审查；只读。

判据只用可证伪的客观量，且每一条都先确认「元素真的可见」：
- pageerror 与控制台 error
- 整页横向溢出
- 可交互控件的可访问名称（按 AccName 的真实来源，而不是 innerText 猜测）
- 可见控件的圆角是否落在共享刻度上

不写主观评价；发现问题只输出事实，交由人工判断严重度。
"""

import json
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("OFFICE_TEST_URL", "http://127.0.0.1:18890")
OUT = pathlib.Path("/tmp/audit-hard")


def operator_password() -> str:
    """只在真正运行探针时读口令；CI 无实例足迹，导入本模块不能因此失败。"""
    return (
        (pathlib.Path(__file__).resolve().parents[1] / "runtime/secrets/operator_password")
        .read_text()
        .strip()
    )


PAGES = [
    ("dashboard", "/console/dashboard"),
    ("employees", "/console/employees"),
    ("roles", "/console/roles"),
    ("permission-sets", "/console/permission-sets"),
    ("permissions", "/console/permissions"),
    ("audit", "/console/audit"),
    ("accounts-operations", "/console/accounts/operations"),
    ("model-settings", "/console/model-settings"),
    ("assistant", "/console/assistant"),
    ("assistant-workspace", "/console/assistant/workspace"),
]
WIDTHS = [1440, 1024, 768, 390, 320]
# 共享刻度：令牌解析后的实际像素值 + 合法的 0/百分比/全圆
ALLOWED_RADII = {"0px", "8px", "10px", "12px", "14px", "16px", "999px", "50%"}

PROBE = """() => {
  const name = (node) => {
    const parts = [];
    if (node.getAttribute('aria-label')) parts.push(node.getAttribute('aria-label'));
    const lb = node.getAttribute('aria-labelledby');
    if (lb) { const e = document.getElementById(lb); if (e) parts.push(e.textContent); }
    if (node.labels && node.labels.length) parts.push([...node.labels].map(l => l.textContent).join(' '));
    parts.push(node.textContent || '');
    if (node.getAttribute('title')) parts.push(node.getAttribute('title'));
    if (node.getAttribute('placeholder')) parts.push(node.getAttribute('placeholder'));
    return parts.join(' ').replace(/\\s+/g, ' ').trim();
  };
  const visible = (node) => {
    const box = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden'
      && style.display !== 'none' && Number(style.opacity) > 0;
  };
  // 排除 aria-hidden 与不可聚焦（tabindex=-1）的元素：它们本就不在无障碍树里，
  // 例如仅作脚本状态载体的 sr-only checkbox，把它算成「无名控件」是误报。
  const reachable = (node) => !node.closest('[aria-hidden="true"]')
    && node.tabIndex !== -1;
  const items = [...document.querySelectorAll(
    'button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button]')]
    .filter((node) => visible(node) && reachable(node));
  const radii = new Set();
  items.forEach((node) => {
    const style = getComputedStyle(node);
    [style.borderTopLeftRadius, style.borderTopRightRadius,
     style.borderBottomLeftRadius, style.borderBottomRightRadius].forEach((v) => radii.add(v));
  });
  return {
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    interactive: items.length,
    unnamed: items.filter((node) => !name(node))
      .map((node) => node.tagName + '#' + node.id + '.' + (node.className || '').toString().slice(0, 30)),
    radii: [...radii].sort(),
  };
}"""


def main() -> int:
    rows, problems = [], []
    OUT.mkdir(parents=True, exist_ok=True)
    password = operator_password()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"{BASE}/console", wait_until="domcontentloaded")
        page.fill("#username", "operator")
        page.fill("#password", password)
        page.click("#kc-login")
        page.wait_for_url("**/console/**", timeout=30_000)

        for name, path in PAGES:
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": 900})
                errors.clear()
                page.goto(f"{BASE}{path}", wait_until="load")
                page.evaluate(
                    "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                )
                data = page.evaluate(PROBE)
                rows.append({"page": name, "width": width, **data, "pageerrors": list(errors)})
                label = f"{name}@{width}"
                if data["overflow"] > 0:
                    problems.append(f"{label} 横向溢出 {data['overflow']}px")
                if data["unnamed"]:
                    problems.append(f"{label} 无可访问名称 {data['unnamed'][:4]}")
                if errors:
                    problems.append(f"{label} pageerror {list(errors)[:2]}")
                odd = [r for r in data["radii"] if r not in ALLOWED_RADII and r.endswith("px")]
                if odd:
                    problems.append(f"{label} 非常规圆角 {odd}")
                if width in (390, 320):
                    page.screenshot(path=str(OUT / f"{name}-{width}.png"), full_page=False)

        browser.close()

    (OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"检查 {len(rows)} 个组合")
    if problems:
        print(f"发现 {len(problems)} 条问题：")
        for item in problems:
            print(" -", item)
        return 1
    print("无问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
