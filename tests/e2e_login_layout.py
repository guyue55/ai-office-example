"""验证登录主题冷加载、刷新及字体延迟时没有文字布局偏移。"""

import json
import os
from pathlib import Path

from e2e_web_console import BASE
from playwright.sync_api import sync_playwright

OBSERVER = """() => {
    window.loginShifts = [];
    window.loginBackgrounds = [];
    window.loginGeometry = [];
    let frames = 0;
    function sample() {
        if (document.querySelector('#username')) {
            window.loginBackgrounds.push(getComputedStyle(document.body).backgroundImage);
            const rect = document.querySelector('main').getBoundingClientRect();
            window.loginGeometry.push({x: rect.x, y: rect.y,
                width: rect.width, height: document.documentElement.scrollHeight,
                viewport: innerHeight, client: document.documentElement.clientWidth});
        }
        if (++frames < 150) requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
    new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
            if (!entry.hadRecentInput) window.loginShifts.push(entry.value);
        }
    }).observe({type: 'layout-shift', buffered: true});
}"""


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
            headless=True,
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        page.add_init_script(f"({OBSERVER})()")
        session = context.new_cdp_session(page)
        session.send("Network.enable")
        session.send("Network.setCacheDisabled", {"cacheDisabled": True})
        # 故意放慢图标等外部字体；文字不得依赖延迟下载的字体。
        page.route("**/*.woff2", lambda route: (page.wait_for_timeout(1200), route.continue_()))
        observations = []
        for width in (1024, 1440, 1920):
            page.set_viewport_size({"width": width, "height": 1000})
            for refresh in range(2):
                if refresh or width != 1024:
                    page.reload()
                else:
                    page.goto(BASE + "/console")
                page.locator("#username").wait_for()
                page.wait_for_timeout(1500)
                shifts = page.evaluate("window.loginShifts")
                # Chromium可报告无来源的亚像素舍入（实测约1e-6）；表单几何仍须完全不变。
                assert sum(shifts) <= 0.00001, (width, refresh, shifts)
                observations.append({"width": width, "refresh": refresh, "cls": sum(shifts)})
                geometry = page.evaluate("window.loginGeometry")
                assert len({(r["x"], r["y"], r["width"]) for r in geometry}) == 1, geometry
                assert all(r["height"] <= r["viewport"] for r in geometry), geometry
                backgrounds = page.evaluate("window.loginBackgrounds")
                assert backgrounds and set(backgrounds) == {"none"}, (width, refresh)
                # 背景绘制变化不会记入布局偏移，必须单独约束，防止漏检闪屏。
                assert (
                    page.locator("body").evaluate(
                        "element => getComputedStyle(element).backgroundImage"
                    )
                    == "none"
                )
                assert (
                    page.locator("body").evaluate(
                        "element => getComputedStyle(element).backgroundColor"
                    )
                    == "rgb(244, 247, 249)"
                )
                assert "RedHat" not in page.locator("main").evaluate(
                    "element => getComputedStyle(element).fontFamily"
                )
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # 模拟较长的认证提示/表单：自然出现滚动条时不得横移或重新居中。
        page.set_viewport_size({"width": 1280, "height": 720})
        page.reload()
        page.locator("#username").wait_for()
        before = page.locator("main").bounding_box()
        page.locator("main").evaluate(
            "element => { const extra = document.createElement('div'); "
            "extra.id = 'layout-long-content'; extra.style.height = '900px'; element.append(extra); }"
        )
        after = page.locator("main").bounding_box()
        assert (before["x"], before["y"]) == (after["x"], after["y"]), (before, after)
        page.locator("#layout-long-content").evaluate("element => element.remove()")
        page.set_viewport_size({"width": 1024, "height": 480})
        assert page.evaluate("document.documentElement.scrollHeight > innerHeight")
        page.locator("#kc-login").scroll_into_view_if_needed()
        assert page.locator("#kc-login").is_visible()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.reload()
        proof = Path(__file__).resolve().parents[1] / "runtime/foundation/layout-review"
        proof.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(proof / "login-stable.png"))
        (proof / "metrics.json").write_text(json.dumps(observations, indent=2))
        browser.close()
    print(
        "登录主题：三种 PC 宽度、六次冷加载/刷新，延迟字体下表单几何不变，累计布局偏移不超过0.00001"
    )


if __name__ == "__main__":
    main()
