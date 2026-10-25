"""Agent可调用的SDK白名单；不复制服务端权限、字段定义或业务模型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    method: str
    arguments: tuple[str, ...] = ()
    required: tuple[str, ...] = ()


# 参数名来自既有SDK，不提供任意method/path、凭证和网络目标。
READS = {
    "whoami": Operation("me"),
    "capabilities": Operation("capabilities"),
    "projects": Operation("projects", ("after", "limit")),
    "schema": Operation("schema", ("project_id", "revision"), ("project_id", "revision")),
    "records": Operation("records", ("project_id", "after", "limit")),
    "record": Operation("record", ("record_id",), ("record_id",)),
    "confirmed": Operation("confirmed", ("record_id",), ("record_id",)),
    "history": Operation("history", ("record_id", "after_revision", "limit"), ("record_id",)),
    "aggregate": Operation(
        "aggregate",
        ("identifier", "revision", "query", "scope"),
        ("identifier", "revision", "query"),
    ),
    "export_confirmed": Operation(
        "export_confirmed", ("project_id", "max_records"), ("project_id",)
    ),
    "access_list": Operation(
        "access_list",
        ("kind", "q", "after", "limit", "page", "actor", "action", "since", "until"),
        ("kind",),
    ),
    "access_detail": Operation(
        "access_detail", ("kind", "identifier", "q", "after", "limit"), ("kind", "identifier")
    ),
    "access_links": Operation(
        "access_links", ("kind", "identifier", "relation"), ("kind", "identifier", "relation")
    ),
    "pending_identities": Operation("pending_identities"),
    "account_capabilities": Operation("account_capabilities"),
    "search_accounts": Operation("search_accounts", ("q",), ("q",)),
    "employee_account": Operation("employee_account", ("identifier",), ("identifier",)),
    "account_operation": Operation("account_operation", ("identifier",), ("identifier",)),
    "account_history": Operation(
        "account_history",
        ("identifier", "q", "status", "kind", "since", "until", "after", "limit", "page"),
    ),
    "authorizations": Operation(
        "client_authorizations",
        (
            "after",
            "limit",
            "scope",
            "q",
            "employee_id",
            "state",
            "client_id",
            "archived",
            "date_field",
            "since",
            "until",
            "page",
        ),
    ),
}

# 前三类复用服务端摘要；其余在固定内容／版本上获取一次真人批准。
WRITES = {
    "data": Operation("apply_data_change", ("change",), ("change",)),
    "access": Operation("apply_access_change", ("change",), ("change",)),
    "account": Operation("submit_account", ("request",), ("request",)),
    "account_resume": Operation(
        "resume_account", ("identifier", "version", "mode"), ("identifier", "version")
    ),
    "submit_record": Operation(
        "submit",
        ("project_id", "schema_revision", "values"),
        ("project_id", "schema_revision", "values"),
    ),
    "confirm_record": Operation("confirm", ("record_id", "version"), ("record_id", "version")),
    "correct_record": Operation(
        "correct",
        ("record_id", "version", "values", "reason"),
        ("record_id", "version", "values", "reason"),
    ),
    "describe_device": Operation("describe_device", ("device_name", "platform")),
    "change_authorization": Operation(
        "change_authorization",
        ("identifier", "version", "action", "reason"),
        ("identifier", "version", "action", "reason"),
    ),
}


def describe():
    return {
        kind: {
            name: {"arguments": list(spec.arguments), "required": list(spec.required)}
            for name, spec in operations.items()
        }
        for kind, operations in (("read", READS), ("write", WRITES))
    }
