"""已实现动作的中文目录；初始化与各入口展示共用，不提供角色授权捷径。"""

DEFINITIONS = {
    "agent.run": ("使用智能助手", "使用已启用的AI能力，仍受本人业务权限与预算限制", "智能助手"),
    "audit.read": ("查看业务与安全审计", "查阅全企业业务审计与已识别人员的权限拒绝事件", "审计"),
    "employee.update": (
        "维护人员姓名",
        "仅修改业务显示姓名，不含启停、身份绑定和授权",
        "人员与授权",
    ),
    "session.read.all": ("查看全部客户端授权", "查阅全企业授权会话与设备备注", "客户端授权"),
    "session.manage.all": ("管理全部客户端授权", "停用、撤销或归档全企业授权会话", "客户端授权"),
    "identity.read": ("查看登录账号", "查看本企业可管理的登录资料与处理进度", "账号安全"),
    "identity.create": ("创建登录账号", "通过受控流程开通员工登录账号", "账号安全"),
    "identity.update": ("维护登录账号", "修改允许的账号资料与登录启停", "账号安全"),
    "identity.credentials.manage": ("重置登录凭证", "在受控浏览器中准备或重置员工凭证", "账号安全"),
    "identity.sessions.revoke": ("结束登录", "结束员工已有登录并撤销旧访问", "账号安全"),
    "access.read": ("查看人员与授权", "查看员工、角色和权限集", "人员与授权"),
    "access.manage": ("管理人员与授权", "管理员工身份、角色和权限集", "人员与授权"),
    "project.read": ("查看项目与字段", "查看普通项目元数据和字段定义", "项目管理"),
    "project.manage": ("维护项目与字段", "维护项目和发布字段定义", "项目管理"),
    "record.confirm": ("确认项目数据", "确认当前读取范围内的候选数据", "项目数据"),
    "record.create": ("上报项目数据", "向项目上报候选数据", "项目数据"),
    "record.read.own": ("查看本人数据", "查看本人上报数据", "项目数据"),
    "record.read.all": ("查看全部数据", "查看所有上报数据", "项目数据"),
    "record.manage.own": ("维护本人数据", "修改或归档本人上报数据", "项目数据"),
    "record.manage.all": ("维护全部数据", "修改或归档所有上报数据", "项目数据"),
}
PERMISSIONS = {code: definition[1] for code, definition in DEFINITIONS.items()}

AUDIT_ACTIONS = {
    "agent.created": "创建智能助手任务",
    "agent.decided": "确认或拒绝助手操作",
    "agent.cancelled": "请求停止助手任务",
    "agent.finished": "智能助手任务结束",
    "security.denied": "权限检查拒绝",
    "session.created": "建立客户端授权",
    "session.device": "更新授权设备备注",
    "session.disable": "停用客户端授权",
    "session.revoke": "撤销客户端授权",
    "session.archive": "归档客户端授权",
    "session.retry": "重试身份会话清理",
    "session.cleanup": "身份会话清理结果",
    "account.submitted": "提交账号操作",
    "account.step": "账号处理进度",
    "account.completed": "账号操作完成",
    "account.attention": "账号操作需处理",
    "account.credential": "准备临时登录凭证",
    "access.changed": "授权配置变更",
    "access.bootstrap": "权限初始化",
    "access.identity.claim": "认领待认领登录",
    "project.created": "创建项目",
    "project.updated": "更新项目",
    "schema.published": "发布字段定义",
    "record.created": "上报数据",
    "record.updated": "修改数据",
    "record.archived": "归档数据",
    "record.confirmed": "确认数据",
    "record.correction.submitted": "提交数据更正",
}

ACCOUNT_ACTIONS = {
    "create": "开通账号",
    "link": "关联账号",
    "update": "修改资料",
    "disable": "停用账号",
    "enable": "启用账号",
    "reset": "重置密码",
    "logout": "结束全部登录",
}
ACCOUNT_STATUSES = {
    "pending": "等待处理",
    "running": "正在处理",
    "awaiting_user": "待准备凭证",
    "succeeded": "处理完成",
    "needs_attention": "需要处理",
    "needs_confirmation": "需要重新确认",
}
ACCOUNT_STEPS = {
    "start": "核实账号",
    "suspend": "结束旧登录",
    "credential": "准备临时密码",
    "credential_uncertain": "密码结果待核实",
    "mail": "准备设密邮件",
    "mail_uncertain": "邮件结果待核实",
    "activate": "完成启用",
    "done": "完成",
}

AUDIT_RESOURCES = {
    "employees": "人员",
    "roles": "角色",
    "permission-sets": "权限集",
    "permissions": "权限",
    "project": "项目",
    "record": "数据记录",
    "record_confirmation": "数据确认",
}


def describe_audit_action(action: str) -> str:
    """未知动作保留原始编码，避免用猜测文案掩盖真实操作。"""
    return AUDIT_ACTIONS.get(action, action)


def describe_audit_detail(action: str, detail: dict) -> str:
    if action == "security.denied":
        return "操作已被服务端拦截：当前职责缺少所需权限"
    kind = str(detail.get("kind", ""))
    operation = str(detail.get("operation", ""))
    revision = detail.get("revision")
    project_id = detail.get("project_id")
    reason = str(detail.get("reason", "")).strip()
    segments: list[str] = []
    if action.startswith("session."):
        if detail.get("employee_name"):
            segments.append("人员：" + detail["employee_name"])
        if detail.get("device_name"):
            segments.append("设备备注：" + detail["device_name"])
        if detail.get("clients"):
            segments.append("客户端：" + "、".join(detail["clients"]))
        if detail.get("cleanup"):
            segments.append(
                {"done": "身份登录已结束", "failed": "清理需重试"}.get(
                    detail["cleanup"], "清理待完成"
                )
            )
    elif action.startswith("account."):
        segments.append(ACCOUNT_ACTIONS.get(kind, kind))
        if detail.get("status"):
            segments.append(ACCOUNT_STATUSES.get(detail["status"], "状态待核实"))
        if detail.get("step"):
            segments.append(ACCOUNT_STEPS.get(detail["step"], "步骤待核实"))
    elif kind:
        segments.append("对象类型：" + AUDIT_RESOURCES.get(kind, kind))
    if operation:
        segments.append({"create": "新建", "update": "修改"}.get(operation, operation))
    relation = str(detail.get("relation", ""))
    if relation:
        segments.append("配置关联：" + AUDIT_RESOURCES.get(relation, relation))
        if "count" in detail:
            segments.append(f"保存后共 {detail['count']} 项")
    if "assigned_role" in detail and (kind == "employees" or action == "access.identity.claim"):
        assigned = detail["assigned_role"]
        segments.append("开通角色：" + assigned["name"] if assigned else "未授予默认角色")
    if revision is not None:
        segments.append(f"版本 {revision}")
    if project_id:
        segments.append("所属项目 " + str(project_id)[:8])
    if reason:
        segments.append("原因：" + reason[:120])
    return "；".join(segments) if segments else "无附加详情"


def describe_permission(code: str, description: str | None = None):
    name, default, group = DEFINITIONS.get(
        code, ("待核对的权限", "请管理员核对服务端权限目录", "其他")
    )
    return {"code": code, "name": name, "description": description or default, "group": group}
