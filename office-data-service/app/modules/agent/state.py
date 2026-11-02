"""助手任务状态与投影，服务和会话共用，避免循环依赖。"""

ACTIVE = {"queued", "running", "awaiting_confirmation"}


def run_view(row, *, detail=True):
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "version",
            "prompt",
            "status",
            "cancel_requested",
            "created_at",
            "updated_at",
        )
    }
    if detail:
        result.update(
            model_revision=row.model_revision,
            progress=getattr(row, "progress", None),
            pending=row.pending,
            result=row.result,
            write_inflight=row.write_inflight,
            conversation_id=row.conversation_id,
            context_page=row.context_page,
            history_hidden=False,
            detail_truncated=False,
        )
    else:
        result["prompt"] = row.prompt[:160]
    return result
