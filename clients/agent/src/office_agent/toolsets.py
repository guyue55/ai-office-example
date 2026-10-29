"""工具分组、按页面预载与按需搜索；可见性只影响上下文，权限仍由服务端逐次核验。"""

import re
from collections.abc import Sequence
from typing import TypedDict


class Group(TypedDict):
    description: str
    tools: list[str]


GROUPS: dict[str, Group] = {
    "core": {
        "description": "身份与数据容器概览",
        "tools": ["read_whoami", "read_capabilities", "read_projects"],
    },
    "data": {
        "description": "项目字段、记录、确认数据、历史版本、多维聚合与导出（只读）",
        "tools": [
            "read_schema",
            "read_records",
            "read_record",
            "read_confirmed",
            "read_history",
            "read_aggregate",
            "read_export_confirmed",
        ],
    },
    "data_write": {
        "description": "项目与记录的变更预览（需真人批准后执行）",
        "tools": [
            "prepare_data",
            "prepare_submit_record",
            "prepare_confirm_record",
            "prepare_correct_record",
        ],
    },
    "access": {
        "description": "人员、角色、权限集、待认领身份与客户端授权（只读）",
        "tools": [
            "read_access_list",
            "read_access_detail",
            "read_access_links",
            "read_pending_identities",
            "read_authorizations",
        ],
    },
    "access_write": {
        "description": "人员与授权变更预览、设备备注（需真人批准后执行）",
        "tools": [
            "prepare_access",
            "prepare_change_authorization",
            "prepare_describe_device",
        ],
    },
    "account": {
        "description": "登录账号查询、账号能力与操作历史（只读）",
        "tools": [
            "read_account_capabilities",
            "read_search_accounts",
            "read_employee_account",
            "read_account_operation",
            "read_account_history",
        ],
    },
    "account_write": {
        "description": "登录账号操作与恢复预览（需真人批准后执行）",
        "tools": ["prepare_account", "prepare_account_resume"],
    },
}

# 搜索用中英文关键词；工具名或描述中的 ASCII 词也参与匹配。
KEYWORDS: dict[str, tuple[str, ...]] = {
    "core": ("身份", "能力", "容器", "whoami", "capabilities", "projects"),
    "data": (
        "项目",
        "字段",
        "记录",
        "历史",
        "聚合",
        "导出",
        "数据",
        "project",
        "schema",
        "record",
        "history",
        "aggregate",
        "export",
    ),
    "data_write": (
        "新建",
        "创建",
        "上报",
        "提交",
        "确认",
        "更正",
        "归档",
        "变更",
        "create",
        "submit",
        "confirm",
        "correct",
        "archive",
    ),
    "access": (
        "权限",
        "人员",
        "角色",
        "授权",
        "审计",
        "成员",
        "permission",
        "role",
        "access",
        "authorization",
        "audit",
    ),
    "access_write": (
        "授权变更",
        "撤销",
        "停用",
        "设备",
        "备注",
        "revoke",
        "device",
        "authorization",
    ),
    "account": ("账号", "登录", "账户", "account", "login", "identity"),
    "account_write": ("开通账号", "重置", "恢复", "邀请", "停用账号", "resume", "reset", "invite"),
}

PROMPT_WRITE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("新建", "创建", "上报", "提交", "确认", "更正", "归档"), "data_write"),
    (("授权变更", "撤销授权", "停用授权", "设备", "备注", "权限调整", "角色变更"), "access_write"),
    (("开通账号", "重置", "恢复账号", "停用账号", "邀请账号"), "account_write"),
)

# 中文同义词按工具补充，避免仅靠英文 token 漏检。
TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "read_pending_identities": ("待认领", "认领", "未绑定", "pending"),
    "read_authorizations": ("客户端授权", "会话", "设备", "授权记录", "authorization"),
    "read_access_list": ("人员", "员工", "角色", "权限集", "权限目录"),
    "read_access_detail": ("权限详情", "授权详情"),
    "read_access_links": ("关联", "直接授权"),
    "read_employee_account": ("员工账号", "账号详情"),
    "read_search_accounts": ("搜索账号", "账号候选"),
    "read_account_history": ("账号历史", "账号操作记录"),
    "read_account_operation": ("账号操作",),
    "read_account_capabilities": ("账号能力",),
    "read_schema": ("字段", "结构", "字段定义"),
    "read_records": ("记录", "数据列表"),
    "read_record": ("记录详情",),
    "read_confirmed": ("确认数据", "已确认"),
    "read_history": ("历史版本", "版本历史"),
    "read_aggregate": ("聚合", "统计", "汇总", "分组"),
    "read_export_confirmed": ("导出",),
    "prepare_data": ("新建项目", "创建项目", "修改项目", "项目变更"),
    "prepare_submit_record": ("上报记录", "提交记录"),
    "prepare_confirm_record": ("确认记录",),
    "prepare_correct_record": ("更正记录", "修改记录"),
    "prepare_access": ("权限变更", "角色调整"),
    "prepare_change_authorization": ("撤销授权", "停用授权", "授权变更"),
    "prepare_describe_device": ("设备备注",),
    "prepare_account": ("账号操作", "开通账号"),
    "prepare_account_resume": ("恢复账号", "账号恢复"),
}

_TOOL_GROUP = {str(tool): name for name, spec in GROUPS.items() for tool in spec["tools"]}
_ASCII = re.compile(r"[a-z0-9]+")


def tool_group(tool_name: str) -> str | None:
    return _TOOL_GROUP.get(tool_name)


def preloaded_tools(context_page: str | None, prompt: str | None = None) -> set[str]:
    """只读能力全部常驻，保证任意页面都能查询；变更预览按明确意图预载。"""
    names: set[str] = set()
    for group, spec in GROUPS.items():
        if not group.endswith("_write"):
            names.update(spec["tools"])
    text = (prompt or "").strip()
    for words, group in PROMPT_WRITE_HINTS:
        if any(word in text for word in words):
            names.update(GROUPS[group]["tools"])
    return names


def deferred_tools(context_page: str | None, prompt: str | None = None) -> set[str]:
    return set(_TOOL_GROUP) - preloaded_tools(context_page, prompt)


def search(_ctx, queries: Sequence[str], tools: Sequence) -> list[str]:
    """中英文关键词搜索；结果有界，只返回本次声明的延迟工具名。"""
    text = " ".join(str(item) for item in queries).lower()[:2000]
    terms = set(_ASCII.findall(text))
    scored: list[tuple[int, str]] = []
    for tool in tools:
        name = tool.name
        group = tool_group(name)
        description = (tool.description or "").lower()
        score = len(terms & set(_ASCII.findall(name + " " + description)))
        if group is not None and any(word.lower() in text for word in KEYWORDS.get(group, ())):
            score += 2
        if any(word.lower() in text for word in TOOL_KEYWORDS.get(name, ())):
            score += 3
        if score:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:10]]
