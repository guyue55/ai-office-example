"""服务器智能助手命令；与可选的本机Agent运行入口区分。"""

from .auth import ClientError
from .local_io import read_json_file


def configure(sub):
    create = sub.add_parser("assistant-conversation-create", help="创建本人的工作会话")
    create.add_argument("--title", default="新会话")
    listing = sub.add_parser("assistant-conversations", help="分页读取本人会话")
    listing.add_argument("--after", default="")
    listing.add_argument("--limit", type=int, default=10)
    listing.add_argument("--archived", action="store_true")
    detail = sub.add_parser("assistant-conversation", help="读取本人会话与有界历史")
    detail.add_argument("identifier")
    detail.add_argument("--before", default="")
    detail.add_argument("--limit", type=int, default=10)
    archive = sub.add_parser(
        "assistant-conversation-archive", help="归档或恢复本人会话；活动任务须先结束"
    )
    archive.add_argument("identifier")
    archive.add_argument("--version", type=int, required=True)
    archive.add_argument("--restore", action="store_true")
    sub.add_parser("assistant-capabilities", help="检查服务器智能助手是否启用")
    start = sub.add_parser("assistant-start", help="按文件启动模型任务，重试保留原请求标识")
    start.add_argument(
        "--file",
        required=True,
        help="含prompt、request_key及可选conversation_id、context_page的JSON",
    )
    listing = sub.add_parser("assistant-runs", help="分页查询本人的服务器任务")
    listing.add_argument("--page", type=int)
    listing.add_argument("--limit", type=int, default=10)
    for key in ("after", "q", "status", "since", "until"):
        listing.add_argument("--" + key, default="")
    get = sub.add_parser("assistant-run", help="读取任务详情、实际回执和完整待批预览")
    get.add_argument("identifier")
    decide = sub.add_parser("assistant-decide", help="真人核对预览后批准或拒绝，不自动推断同意")
    decide.add_argument("identifier")
    decide.add_argument("--version", type=int, required=True)
    decide.add_argument("--proposal-id", required=True)
    decide.add_argument("--decision", choices=("approve", "deny"), required=True)
    cancel = sub.add_parser("assistant-cancel", help="请求停止本人任务，已完成的操作不回滚")
    cancel.add_argument("identifier")
    cancel.add_argument("--version", type=int, required=True)


def execute(client, args):
    match args.action:
        case "assistant-conversation-create":
            return True, client.assistant_conversation_create(args.title)
        case "assistant-conversations":
            return True, client.assistant_conversations(
                after=args.after, limit=args.limit, archived=args.archived
            )
        case "assistant-conversation":
            return True, client.assistant_conversation(
                args.identifier, before=args.before, limit=args.limit
            )
        case "assistant-conversation-archive":
            return True, client.assistant_conversation_archive(
                args.identifier, args.version, not args.restore
            )
        case "assistant-capabilities":
            return True, client.assistant_capabilities()
        case "assistant-start":
            payload = read_json_file(args.file)
            if not {"prompt", "request_key"} <= set(payload) or set(payload) - {
                "prompt",
                "request_key",
                "conversation_id",
                "context_page",
            }:
                raise ClientError("任务文件仅接受prompt、request_key及可选会话与页面标签")
            options = {
                key: payload[key] for key in ("conversation_id", "context_page") if key in payload
            }
            return True, client.assistant_start(
                payload["prompt"], payload["request_key"], **options
            )
        case "assistant-runs":
            return True, client.assistant_runs(
                **{
                    key: getattr(args, key)
                    for key in ("page", "limit", "after", "q", "status", "since", "until")
                }
            )
        case "assistant-run":
            return True, client.assistant_run(args.identifier)
        case "assistant-decide":
            return True, client.assistant_decide(
                args.identifier, args.version, args.proposal_id, args.decision == "approve"
            )
        case "assistant-cancel":
            return True, client.assistant_cancel(args.identifier, args.version)
        case _:
            return False, None
