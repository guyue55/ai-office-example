"""任务详情页的进度轮询边界：只在 queued/running 时自动核对，等待本人决定时不打断。"""

import sys
from pathlib import Path

import pytest
from app.entrypoints.web.responses import TEMPLATES
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_catalog import context

REQUEST = Request(
    {
        "type": "http",
        "method": "GET",
        "path": "/console/assistant/task",
        "query_string": b"",
        "headers": [],
    }
)


def render(status: str, *, cancel_requested: bool = False) -> str:
    values = context("assistant_task.html")
    values["task"] = dict(values["task"], status=status, cancel_requested=cancel_requested)
    values["request"] = REQUEST
    return TEMPLATES.env.get_template("assistant_task.html").render(**values)


@pytest.mark.parametrize("status", ["queued", "running"])
def test_running_task_page_polls_progress(status):
    html = render(status)
    assert "任务详情本身不自动更新" in html
    assert "页面每 5 秒核对一次任务进度。" in html


@pytest.mark.parametrize("status", ["awaiting_confirmation", "completed", "failed"])
def test_settled_or_deciding_task_page_does_not_poll(status):
    html = render(status)
    assert "任务详情本身不自动更新" not in html
    assert "页面每 5 秒核对一次任务进度。" not in html


def test_awaiting_confirmation_keeps_preview_readable():
    html = render("awaiting_confirmation")
    assert "请核对预览后提交决定。" in html
    assert 'name="decision"' in html


def test_cancelled_running_task_stops_polling():
    html = render("running", cancel_requested=True)
    assert "任务详情本身不自动更新" not in html
