"""扩展命令与共用筛选；不复制认证、HTTP或重试逻辑。"""

import hashlib
import hmac
import json

from .auth import ClientError, trusted_url
from .local_io import read_json_file


def paging(parser, *, numbered=False, default=50):
    parser.add_argument("--after", default="")
    parser.add_argument("--limit", type=int, default=default)
    if numbered:
        parser.add_argument("--page", type=int)


def dates(parser):
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")


def configure(sub):
    aggregate = sub.add_parser("aggregate", help="按已发布结构执行只读聚合，返回覆盖信息")
    aggregate.add_argument("identifier")
    aggregate.add_argument("--revision", type=int, required=True)
    aggregate.add_argument("--scope", choices=("own", "all"), default="own")
    aggregate.add_argument("--file", required=True, help="受控查询JSON文件，不接受SQL")
    export = sub.add_parser("export-confirmed", help="单项目有界导出已确认数据，不包含未确认候选")
    export.add_argument("project_id")
    export.add_argument("--max-records", type=int, default=1000)
    sub.add_parser("pending-identities", help="查询待认领身份，最多50项")
    command = sub.add_parser(
        "project-preview", help="生成带创建请求标识的项目预览，保存返回change后确认"
    )
    command.add_argument("name")
    command.add_argument("--idempotency-key")
    for name in ("data-preview", "data-apply"):
        command = sub.add_parser(name, help="封闭的数据管理变更；先预览，人工确认后执行")
        command.add_argument("--file", required=True)
        if name == "data-apply":
            command.add_argument("--confirm-digest", required=True)
    for name in ("projects", "records"):
        command = sub.add_parser(name)
        paging(command)
        if name == "records":
            command.add_argument("--project-id")
    history = sub.add_parser("account-history", help="分页筛选账号操作历史")
    paging(history, numbered=True, default=20)
    dates(history)
    for key in ("identifier", "q", "status", "kind"):
        history.add_argument("--" + key, default=None if key == "identifier" else "")
    resume = sub.add_parser("account-resume-preview", help="只预览恢复，不继续执行")
    resume.add_argument("identifier")
    resume.add_argument("--mode", choices=("temporary", "email"))
    resume = sub.add_parser("account-resume", help="按用户确认的预览继续原操作")
    resume.add_argument("identifier")
    resume.add_argument("--version", type=int, required=True)
    resume.add_argument("--confirm-digest", required=True)
    resume.add_argument("--mode", choices=("temporary", "email"))
    for name in ("record", "record-confirmed", "record-history"):
        command = sub.add_parser(name)
        command.add_argument("identifier")
        if name == "record-history":
            command.add_argument("--after-revision", type=int, default=0)
            command.add_argument("--limit", type=int, default=50)
    for name in ("record-confirm", "record-correct"):
        command = sub.add_parser(name, help="先用preview取得文件摘要，用户确认后执行")
        command.add_argument("--file", required=True)
        command.add_argument("--confirm-digest", required=True)


def confirmed_file(args):
    payload = read_json_file(args.file)
    digest = file_digest(args.service, payload)
    if not hmac.compare_digest(digest, args.confirm_digest):
        raise ClientError("操作内容与已确认摘要不一致，请重新预览和确认")
    return payload


def file_digest(service, payload):
    return hashlib.sha256(
        json.dumps(
            {"service": trusted_url(service), "payload": payload},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def execute(client, args):
    """返回(是否处理, 结果)，不将未匹配命令降级为任意请求。"""
    action = args.action
    if action == "aggregate":
        query = read_json_file(args.file)
        return True, client.aggregate(args.identifier, args.revision, query, scope=args.scope)
    if action == "export-confirmed":
        return True, client.export_confirmed(args.project_id, args.max_records)
    if action == "pending-identities":
        return True, client.pending_identities()
    if action == "project-preview":
        return True, client.preview_project(args.name, idempotency_key=args.idempotency_key)
    if action in {"data-preview", "data-apply"}:
        change = read_json_file(args.file)
        return True, (
            client.preview_data_change(change)
            if action == "data-preview"
            else client.apply_data_change(change, args.confirm_digest)
        )
    if action == "projects":
        return True, client.projects(after=args.after, limit=args.limit)
    if action == "records":
        return True, client.records(project_id=args.project_id, after=args.after, limit=args.limit)
    if action == "account-history":
        return True, client.account_history(
            **{
                key: getattr(args, key)
                for key in (
                    "identifier",
                    "q",
                    "status",
                    "kind",
                    "since",
                    "until",
                    "after",
                    "limit",
                    "page",
                )
            }
        )
    if action == "account-resume-preview":
        return True, client.preview_account_resume(args.identifier, args.mode)
    if action == "account-resume":
        return True, client.resume_account(
            args.identifier, args.version, args.confirm_digest, args.mode
        )
    if action == "record":
        return True, client.record(args.identifier)
    if action == "record-confirmed":
        return True, client.confirmed(args.identifier)
    if action == "record-history":
        return True, client.history(
            args.identifier, after_revision=args.after_revision, limit=args.limit
        )
    if action in {"record-confirm", "record-correct"}:
        payload = confirmed_file(args)
        if action == "record-confirm":
            if set(payload) != {"record_id", "version"}:
                raise ClientError("确认文件仅接受record_id和version")
            return True, client.confirm(payload["record_id"], payload["version"])
        if set(payload) != {"record_id", "version", "values", "reason"}:
            raise ClientError("更正文件必须包含record_id、version、values、reason")
        return True, client.correct(
            payload["record_id"],
            version=payload["version"],
            values=payload["values"],
            reason=payload["reason"],
        )
    return False, None
