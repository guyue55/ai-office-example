"""全模板视觉样本：只构造合成展示上下文，不冒充业务或身份集成验收。"""

from datetime import UTC, datetime

from app.entrypoints.web.assistant import OPERATIONS, OUTCOMES, STATUSES
from app.entrypoints.web.console import ACCESS_LABELS
from app.modules.access.catalog import (
    ACCOUNT_ACTIONS,
    ACCOUNT_STATUSES,
    AUDIT_ACTIONS,
    DEFINITIONS,
    describe_permission,
)

PAGE_PATHS = {
    "model_settings.html": "/console/model-settings",
    "access_detail.html": "/console/access/employees/person",
    "account_credential.html": "/console/accounts/operations/operation/credential",
    "account_detail.html": "/console/accounts/employees/person",
    "account_link.html": "/console/accounts/link",
    "account_new.html": "/console/accounts/new",
    "account_operation.html": "/console/accounts/operations/operation",
    "account_operations.html": "/console/accounts/operations",
    "account_preview.html": "/console/accounts/submit",
    "account_security.html": "/console/security",
    "audit.html": "/console/audit",
    "client_authorizations.html": "/console/client-authorizations",
    "dashboard.html": "/console/dashboard",
    "employees.html": "/console/employees",
    "error.html": "/console/roles",
    "links.html": "/console/links/employees/person/roles",
    "permission_sets.html": "/console/permission-sets",
    "permissions.html": "/console/permissions",
    "roles.html": "/console/roles",
    "signed_out.html": "/console/signed-out",
    "assistant.html": "/console/assistant",
    "assistant_task.html": "/console/assistant/task",
    "assistant_workspace.html": "/console/assistant/workspace",
}


def context(template):
    now = datetime(2026, 10, 26, 8, tzinfo=UTC)
    permissions = [describe_permission(code) for code in DEFINITIONS]
    person = {
        "id": "person",
        "name": "跨部门协作测试人员",
        "login_name": "catalog-person",
        "active": True,
        "version": 2,
        "role_count": 1,
    }
    role = {
        "id": "role",
        "name": "资料查阅角色",
        "version": 2,
        "permission_count": 5,
        "employee_count": 2,
        "set_count": 1,
        "role_count": 1,
    }
    pagination = {"page": 1, "page_size": 10, "total": 1, "pages": 1, "start": 1, "end": 1}
    listing = {"items": [person], "next": None, "has_more": False, "pagination": pagination}
    operation = {
        "id": "operation",
        "version": 2,
        "kind": "create",
        "status": "succeeded",
        "message": "合成账号操作已处理",
        "username": "catalog-person",
        "employee_name": person["name"],
        "employee_id": "person",
        "updated_at": now,
        "next_url": "/console/accounts/operations/operation",
    }
    task = {
        "id": "task",
        "version": 2,
        "prompt": "核查本人获准查看的数据，预览后由本人确认。",
        "status": "awaiting_confirmation",
        "updated_at": now,
        "cancel_requested": False,
        "pending": {
            "ticket": "catalog-ticket",
            "operation": "submit_record",
            "arguments": {"values": {"说明": "仅合成样本"}},
            "preview": {"before": {}, "after": {"名称": "合成候选"}},
        },
        "result": {
            "explanation": "操作是否完成以实际回执为准。",
            "receipts": [
                {"operation": "records", "status": "read", "result": {"count": 1, "complete": True}}
            ],
        },
    }
    value = {
        "page_template": template,
        "me": {
            "id": "catalog-admin",
            "name": "隔离巡视管理员",
            "permissions": list(DEFINITIONS),
            "roles": [role],
        },
        "csrf": "catalog-csrf",
        "trace_id": "catalog-trace",
        "demo_mode": True,
        "assistant_statuses": STATUSES,
        "assistant_outcomes": OUTCOMES,
        "assistant_operations": OPERATIONS,
        "statuses": STATUSES,
        "operations": OPERATIONS,
        "can_manage": True,
        "can_update_employee": True,
        "accounts_enabled": True,
        "access_labels": ACCESS_LABELS,
        "audit_actions": AUDIT_ACTIONS,
        "account_actions": ACCOUNT_ACTIONS,
        "account_statuses": ACCOUNT_STATUSES,
        "permission_details": permissions,
        "listing": listing,
        "items": [person],
        "candidates": [role],
        "pending": {"items": []},
        "kind": "employees",
        "relation": "roles",
        "identifier": "person",
        "assoc": {"ids": ["role"], "version": 2},
        "preserved_items": [],
        "detail": {
            **person,
            "employee_id": "person",
            "available": True,
            "security_pending": False,
            "account": {"username": "catalog-person", "email": "", "enabled": True},
            "links": [role],
            "permissions": permissions[:5],
            "used_by": {"items": [person], "next": None, "has_more": False},
        },
        "history": {"items": [operation]},
        "operation": operation,
        "capability": {
            "ready": False,
            "enabled": True,
            "email_enabled": False,
            "message": "巡视环境未配置模型。",
        },
        "found": {
            "notice": "以下是合成账号，请核对后再关联。",
            "items": [
                {
                    "id": "remote",
                    "name": person["name"],
                    "username": "catalog-person",
                    "email": "",
                    "enabled": True,
                    "linked_employee_id": None,
                }
            ],
        },
        "preview": {
            "effects": ["创建合成账号", "分配普通员工基础角色"],
            "request": {"username": "catalog-person"},
            "confirmation_digest": "catalog-digest",
        },
        "request_json": "{}",
        "key": "catalog-key",
        "resume": None,
        "recent": False,
        "task": task,
        "request_key": "catalog-request",
        "search": "",
        "message": "配置已被其他人员更新，请核对最新版本。",
        "code": 409001,
        "conflict": True,
        "recovery_path": "/console/roles",
        "logout_cleanup": "done",
        "password": "synthetic-display-only",
        "returned": False,
        "filter_mode": {
            "audit.html": "audit",
            "client_authorizations.html": "authorizations",
            "account_operations.html": "operations",
        }.get(template, ""),
    }
    if template == "account_preview.html":
        value["preview"] = {
            "effects": ["修改指定资料，不改动角色或凭证"],
            "request": {
                "kind": "update",
                "identifier": "person",
                "version": 2,
                "name": "更新后的业务姓名",
                "email": "new@example.invalid",
            },
            "confirmation_digest": "catalog-digest",
        }
        value["target"] = {
            "name": person["name"],
            "version": 2,
            "account": {"username": "catalog-person", "email": "old@example.invalid"},
        }
    if template in {"roles.html", "permission_sets.html"}:
        value["items"] = [role]
    if template == "permissions.html":
        value["items"] = permissions[:10]
    if template == "audit.html":
        value["items"] = [
            {
                "created_at": now,
                "actor_name": "隔离巡视管理员",
                "action_name": "授权配置变更",
                "action": "access.changed",
                "resource_name": person["name"],
                "detail_summary": "调整合成角色关联",
                "detail": {"before": [], "after": ["role"]},
                "trace_id": "catalog-trace",
            }
        ]
    if template == "account_operations.html":
        listing["items"] = [operation]
    if template == "assistant.html":
        listing["items"] = [task]
    if template == "client_authorizations.html":
        listing["items"] = [
            {
                "id": "session",
                "version": 1,
                "employee_id": "person",
                "employee_name": person["name"],
                "clients": ["office-cli"],
                "device_name": "隔离测试电脑",
                "platform": "Windows／macOS",
                "current": False,
                "created_at": now,
                "last_seen_at": now,
                "authenticated_at": now,
                "token_expires_at": now,
                "ended_at": None,
                "archived_at": None,
                "state": "active",
                "cleanup": "not_requested",
            }
        ]
    if template == "model_settings.html":
        from app.modules.agent.model_configuration import ModelLimits

        value.update(
            configuration={
                "version": 0,
                "draft_revision": None,
                "active_revision": None,
                "source": "external",
                "installed": True,
                "ready": False,
                "secret_store_ready": True,
                "secret_set": False,
                "message": "合成巡视尚未配置模型",
                "allowed_urls": ["https://model.invalid/v1"],
            },
            limits=ModelLimits().model_dump(),
            draft={},
        )
    return value
