"""页面归属与位置名称；服务端、无脚本导航与位置栏共用一份定义。"""

SECTIONS = {
    "dashboard": "仪表盘",
    "employees": "人员管理",
    "roles": "角色",
    "permission-sets": "权限集",
    "permissions": "权限目录",
    "audit": "审计",
    "client-authorizations": "客户端授权",
    "assistant": "智能助手",
    "account-operations": "账号操作记录",
}


TEMPLATE_TITLES = {
    "account_preview.html": "核对账号操作",
    "account_credential.html": "临时密码已准备",
    "account_security.html": "账号与安全",
    "error.html": "操作未成功",
    "signed_out.html": "已退出控制台",
}


def page_location(path: str, template: str = "") -> dict[str, str]:
    parts = path.strip("/").split("/")
    section = parts[1] if len(parts) > 1 else ""
    title = "管理控制台"
    if section == "security":
        return {
            "section": "",
            "parent": "当前账号",
            "title": TEMPLATE_TITLES.get(template, "账号与安全"),
        }
    if section in {"access", "links"} and len(parts) > 2:
        section = parts[2]
        title = "授权详情" if parts[1] == "access" else "授权关联"
    elif section == "accounts":
        item = parts[2] if len(parts) > 2 else ""
        section = "account-operations" if item == "operations" else "employees"
        title = {
            "new": "新建员工",
            "link": "关联已有账号",
            "employees": "账号详情",
            "submit": "核对账号操作",
            "operations": "账号处理进度",
            "security": "账号与安全",
        }.get(item, "人员与账号")
        if item == "operations" and len(parts) == 3:
            title = SECTIONS[section]
    elif section == "assistant":
        title = (
            "智能助手"
            if path.endswith("/workspace")
            else "任务记录"
            if len(parts) == 2
            else "任务详情"
        )
    elif section in SECTIONS:
        title = SECTIONS[section]
        if len(parts) > 2:
            title = "新建" + title if parts[2] == "new" else title + "详情"
    return {
        "section": section,
        "parent": SECTIONS.get(section, "工作台"),
        "title": TEMPLATE_TITLES.get(template, title),
    }
