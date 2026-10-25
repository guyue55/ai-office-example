"""服务器智能助手命令；与可选的本机Agent运行入口区分。"""

from .auth import ClientError
from .local_io import read_json_file


def configure(sub):
    sub.add_parser("assistant-capabilities", help="检查服务器智能助手是否启用")
    start = sub.add_parser("assistant-start", help="按文件启动模型任务，重试保留原请求标识")
    start.add_argument("--file", required=True, help="仅含prompt、request_key的JSON")
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
        case "assistant-capabilities":
            return True, client.assistant_capabilities()
        case "assistant-start":
            payload = read_json_file(args.file)
            if set(payload) != {"prompt", "request_key"}:
                raise ClientError("任务文件仅接受prompt、request_key")
            return True, client.assistant_start(payload["prompt"], payload["request_key"])
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
