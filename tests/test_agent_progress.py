"""展示快照与业务版本隔离，旧结果兼容。"""

from datetime import UTC, datetime

from app.modules.agent.outputs import RunView


def test_run_view_exposes_bounded_progress_without_requiring_it_for_old_tasks():
    now = datetime.now(UTC)
    values = {
        "id": "run",
        "version": 1,
        "prompt": "测试",
        "status": "running",
        "cancel_requested": False,
        "created_at": now,
        "updated_at": now,
        "pending": None,
        "result": None,
        "write_inflight": False,
    }
    old = RunView.model_validate(values)
    assert old.progress is None
    progress = {
        "schema_version": 1,
        "seq": 2,
        "elapsed_ms": 30,
        "stage": "reply",
        "text": "已读取",
        "steps": [],
        "truncated": False,
    }
    new = RunView.model_validate({**values, "progress": progress})
    assert new.progress.seq == 2


def test_stream_setting_is_explicit_and_shared_with_managed_configuration():
    from app.modules.agent.model_configuration import ModelDraft
    from office_agent.config import AgentConfig

    draft = ModelDraft(
        model="test",
        api="openai-completions",
        model_url="https://model.invalid/v1",
        stream_output=False,
        approved_model_data=True,
    )
    config = AgentConfig(service_url="https://office.invalid", **draft.model_dump())
    assert config.stream_output is False
