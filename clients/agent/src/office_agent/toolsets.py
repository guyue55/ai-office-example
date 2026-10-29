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

PAGE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("权限", "人员", "角色", "审计", "授权"), "access"),
    (("账号", "登录"), "account"),
    (("项目", "数据", "记录", "仪表"), "data"),
)

PROMPT_WRITE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("新建", "创建", "上报", "提交", "确认", "更正", "归档"), "data_write"),
    (("授权变更", "撤销授权", "停用授权", "设备", "备注", "权限调整", "角色变更"), "access_write"),
    (("开通账号", "重置", "恢复账号", "停用账号", "邀请账号"), "account_write"),
)

_TOOL_GROUP = {str(tool): name for name, spec in GROUPS.items() for tool in spec["tools"]}
_ASCII = re.compile(r"[a-z0-9]+")


def tool_group(tool_name: str) -> str | None:
    return _TOOL_GROUP.get(tool_name)


def preloaded_tools(context_page: str | None, prompt: str | None = None) -> set[str]:
    """页面决定默认读取组；明确的变更意图预载对应预览组，避免多一次搜索。"""
    names: set[str] = set(GROUPS["core"]["tools"])
    page = (context_page or "").strip()
    for words, group in PAGE_KEYWORDS:
        if any(word in page for word in words):
            names.update(GROUPS[group]["tools"])
            break
    else:
        names.update(GROUPS["data"]["tools"])
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
        if score:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:10]]
