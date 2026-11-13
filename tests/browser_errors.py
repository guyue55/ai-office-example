"""浏览器 pageerror 收集：只放过浏览器内部的能力性中止，不放宽应用缺陷判定。

跨文档 view transition 进行中改变视口尺寸时，Chromium 会中止过渡并以 pageerror
形式上报 `Transition was aborted because of invalid state. Viewport size changed`。
这是浏览器的正常降级路径，不代表应用出错；但按视口分档做响应式验收的测试会在
`assert not errors` 上偶发失败（目前只在 CI 的 ubuntu Chromium 上观测到，本地
macOS 未能复现）。

因此这里只放过这一条精确消息，其余任何 pageerror 仍全部计入失败：一旦放宽成
忽略全部 pageerror，这层回归就失去意义。
"""

from __future__ import annotations

from typing import Any

# 必须与 Chromium 的原文逐字一致；改成模糊匹配会把真实缺陷一起吞掉。
VIEW_TRANSITION_RESIZE = "Transition was aborted because of invalid state. Viewport size changed"


def is_browser_internal(message: str) -> bool:
    """只判定已知的浏览器内部消息，不做前缀或子串泛化匹配。"""
    return VIEW_TRANSITION_RESIZE in message


def collect(page: Any) -> list[str]:
    """挂载 pageerror 收集器并返回列表；浏览器内部中止不入列。"""
    errors: list[str] = []

    def record(error: Any) -> None:
        text = str(error)
        if is_browser_internal(text):
            return
        errors.append(text)

    page.on("pageerror", record)
    return errors
