"""本人助手工作台；复用应用授权、CSRF、分页、提示和确认弹窗。"""

from uuid import uuid4

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.common.errors import DomainError
from app.entrypoints.web.tables import table_query
from app.modules.agent.schemas import RunList

STATUSES = {
    "queued": "等待执行",
    "running": "正在处理",
    "awaiting_confirmation": "等待本人确认",
    "completed": "处理结束",
    "failed": "处理失败",
    "cancelled": "已停止",
    "interrupted": "运行已中断",
    "outcome_unknown": "执行结果待核实",
}
OPERATIONS = {
    "data": "维护数据容器、字段或记录",
    "access": "调整人员与权限",
    "account": "维护登录账号",
    "account_resume": "继续未完成的账号操作",
    "submit_record": "上报候选数据",
    "confirm_record": "确认并冻结数据",
    "correct_record": "提交数据更正候选",
    "describe_device": "修改当前设备备注",
    "change_authorization": "变更客户端授权",
}


def register_assistant(router, operate, render, csrf, version, notify):
    @router.get("/console/assistant")
    async def listing(request: Request):
        result = await operate(request, "agent.list", table_query(request, RunList.model_fields))
        capability = await operate(request, "agent.capabilities")
        return await render(
            request,
            "assistant.html",
            listing=result,
            capability=capability,
            statuses=STATUSES,
            request_key=str(uuid4()),
        )

    @router.post("/console/assistant")
    async def start(request: Request):
        form = dict(await request.form())
        csrf(request, form)
        result = await operate(
            request,
            "agent.start",
            {"prompt": form.get("prompt", ""), "request_key": form.get("request_key", "")},
        )
        notify(request, "任务已受理，请查看处理状态；实际写入仍需本人核对预览。")
        return RedirectResponse("/console/assistant/" + result["id"], status_code=303)

    @router.get("/console/assistant/{identifier}")
    async def detail(request: Request, identifier: str):
        task = await operate(request, "agent.get", {"identifier": identifier})
        return await render(
            request, "assistant_task.html", task=task, statuses=STATUSES, operations=OPERATIONS
        )

    @router.post("/console/assistant/{identifier}/decision")
    async def decision(request: Request, identifier: str):
        form = dict(await request.form())
        csrf(request, form)
        if form.get("decision") not in {"approve", "deny"}:
            raise DomainError(422, "invalid_input", "请选择批准或拒绝")
        await operate(
            request,
            "agent.decide",
            {
                "identifier": identifier,
                "version": version(form),
                "proposal_id": form.get("proposal_id", ""),
                "approve": form["decision"] == "approve",
            },
        )
        notify(request, "决定已保存；请刷新查看实际结果，未返回回执前不要重复执行。")
        return RedirectResponse("/console/assistant/" + identifier, status_code=303)

    @router.post("/console/assistant/{identifier}/cancel")
    async def cancel(request: Request, identifier: str):
        form = dict(await request.form())
        csrf(request, form)
        await operate(request, "agent.cancel", {"identifier": identifier, "version": version(form)})
        notify(request, "已请求停止；已完成的操作保留，在途写入请核对实际回执。")
        return RedirectResponse("/console/assistant/" + identifier, status_code=303)
