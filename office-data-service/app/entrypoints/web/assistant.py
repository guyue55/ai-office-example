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
# Web侧栏与完整任务页共用的结果说明，只描述系统状态，不采信模型声称成功。
OUTCOMES = {
    "completed": "本轮任务已完成，请核对实际回执。",
    "failed": "本轮未完成，请核对任务详情和实际回执；不会自动重试。",
    "interrupted": "任务已中断，未自动恢复；请核对实际回执后再发起新任务。",
    "cancelled": "任务已停止；已完成的操作不会撤回。",
    "outcome_unknown": "写入结果待核实，请先检查实际数据，不要重复提交。",
    "queued": "任务已排队，尚未开始。",
    "running": "任务正在处理，结果以实际回执为准。",
    "awaiting_confirmation": "有操作等待您核对，批准后才会继续。",
}

OPERATIONS = {
    "list_toolgroups": "查看可加载的工具组",
    "activate_toolgroup": "加载工具组",
    "whoami": "查看本人身份与权限",
    "capabilities": "查看可用能力",
    "projects": "查看数据容器",
    "schema": "查看字段定义",
    "records": "查询记录",
    "record": "查看记录详情",
    "confirmed": "查看确认数据",
    "history": "查看历史版本",
    "aggregate": "多维聚合查询",
    "export_confirmed": "导出已确认数据",
    "access_list": "查询人员与权限",
    "access_detail": "查看授权详情",
    "access_links": "查看授权关联",
    "pending_identities": "查看待认领身份",
    "account_capabilities": "检查账号管理能力",
    "search_accounts": "搜索登录账号",
    "employee_account": "查看关联账号",
    "account_operation": "查看账号操作",
    "account_history": "查看账号操作历史",
    "authorizations": "查看客户端授权",
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
