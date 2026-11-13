"""浏览器错误收集器自身的回归：只放过已知浏览器内部中止。"""

from browser_errors import VIEW_TRANSITION_RESIZE, collect, is_browser_internal


class FakePage:
    def __init__(self) -> None:
        self.handlers = []

    def on(self, event, handler):
        assert event == "pageerror"
        self.handlers.append(handler)

    def emit(self, message: str) -> None:
        for handler in self.handlers:
            handler(Exception(message))


def test_view_transition_resize_is_ignored() -> None:
    """Chromium 中止跨文档过渡属能力性降级，不算应用缺陷。"""
    page = FakePage()
    errors = collect(page)
    page.emit(VIEW_TRANSITION_RESIZE)
    assert errors == []


def test_real_errors_are_still_collected() -> None:
    """不能因为放过一条浏览器消息就放宽整体判定。"""
    page = FakePage()
    errors = collect(page)
    page.emit("TypeError: cannot read properties of null")
    assert errors == ["TypeError: cannot read properties of null"]


def test_filter_is_exact_not_fuzzy() -> None:
    """前缀或近似文本必须仍算错误，避免模糊匹配吞掉真实缺陷。"""
    assert not is_browser_internal("Transition was aborted")
    assert not is_browser_internal("Viewport size changed")
    assert not is_browser_internal("Failed to execute 'startViewTransition'")
    assert is_browser_internal(VIEW_TRANSITION_RESIZE)
