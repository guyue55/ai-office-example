"""供技能与员工调用的窄入口；不打印访问令牌。"""

import hmac
import json
import sys
from importlib import import_module
from importlib.metadata import version

import httpx

from .auth import ClientError
from .cli_commands import configure, execute, file_digest
from .cli_presentation import ConsoleParser, configure_help
from .client import OfficeClient, ServiceError
from .connection import ConnectionCheckError, check_connection
from .diagnostics import diagnose
from .local_io import configure_cli_output, read_json_file
from .product import PRODUCT


def build_parser():
    parser = ConsoleParser(
        prog=PRODUCT["command"],
        description=PRODUCT["cli_name"] + " · 登录授权、项目数据与人员权限管理",
        usage="%(prog)s [全局选项] <命令> [命令选项]",
    )
    parser.add_argument(
        "--version",
        action="version",
        help="显示客户端版本并退出",
        version=PRODUCT["command"] + " " + version("office-client"),
    )
    parser.add_argument("--service", default=None, help="企业服务地址；未配置时需先通过AI引导设置")
    parser.add_argument(
        "--lan-http-demo", action="store_true", help="显式允许私有IPv4明文HTTP，仅供开发演示"
    )
    parser.add_argument("--config", help="读取安装器生成的公开连接JSON")
    sub = parser.add_subparsers(dest="action", metavar="命令", prog=parser.prog)
    agent = sub.add_parser("agent", help="运行可选的独立员工端Agent")
    agent.add_argument("--agent-config", required=True, help="可信Agent TOML配置")
    agent.add_argument("--prompt", required=True, help="单次自然语言任务，请勿输入秘密")
    for name in ("logout", "whoami", "capabilities", "auth-status"):
        sub.add_parser(name)
    login = sub.add_parser("login", help="浏览器PKCE授权；授权链接仅供同机浏览器使用")
    login.add_argument(
        "--no-browser", action="store_true", help="显示链接但不自动打开浏览器，进程仍等待同机回调"
    )
    login.add_argument("--timeout", type=int, default=120)
    sub.add_parser("connection-check", help="匿名检查服务和身份入口，不读取凭证")
    sub.add_parser("doctor", help="离线检查解释器、依赖和安全后端，不读取凭证或联网")
    configure(sub)
    auth_list = sub.add_parser("authorizations", help="分页筛选本人或全企业授权")
    auth_list.add_argument("--scope", choices=("own", "all"), default="own")
    auth_list.add_argument("--limit", type=int, default=20)
    for key in ("after", "q", "state", "client-id", "since", "until"):
        auth_list.add_argument("--" + key, default="")
    auth_list.add_argument("--archived", action="store_true")
    auth_list.add_argument("--page", type=int)
    auth_list.add_argument("--employee-id")
    auth_list.add_argument(
        "--date-field", choices=("created_at", "last_seen_at"), default="created_at"
    )
    device = sub.add_parser("describe-device", help="为当前授权登记设备备注")
    device.add_argument("name")
    device.add_argument("--platform", default="")
    change = sub.add_parser("authorization-change", help="确认后停止/归档授权，不可重新激活旧凭证")
    change.add_argument("identifier")
    change.add_argument("--version", type=int, required=True)
    change.add_argument(
        "--action",
        dest="session_action",
        choices=("disable", "revoke", "archive", "retry"),
        required=True,
    )
    change.add_argument("--reason", required=True)
    change.add_argument(
        "--confirm", action="store_true", help="已核对同次浏览器登录的客户端将一起失效"
    )
    schema = sub.add_parser("schema")
    schema.add_argument("project_id")
    schema.add_argument("revision", type=int)
    kinds = ("employees", "roles", "permission-sets")
    access_list = sub.add_parser("access-list", help="搜索及分页读取人员、角色、权限集、权限或审计")
    access_list.add_argument("kind", choices=(*kinds, "permissions", "audit"))
    access_list.add_argument("--q", default="")
    access_list.add_argument("--after", default="")
    access_list.add_argument("--limit", type=int, default=50)
    access_list.add_argument("--page", type=int)
    for key in ("actor", "action", "since", "until"):
        access_list.add_argument("--" + key, dest="filter_" + key, default="")
    detail = sub.add_parser("access-detail", help="查看授权详情、中文权限说明和使用者")
    detail.add_argument("kind", choices=kinds)
    detail.add_argument("identifier")
    detail.add_argument("--after", default="")
    detail.add_argument("--limit", type=int, default=50)
    links = sub.add_parser("access-links", help="读取完整直接关联与当前版本")
    links.add_argument("kind", choices=kinds)
    links.add_argument("identifier")
    links.add_argument("relation", choices=("roles", "permission-sets", "permissions"))
    for action in ("access-preview", "access-apply"):
        command = sub.add_parser(action, help="预览或确认执行管理变更；禁止未预览直接写入")
        command.add_argument(
            "--file", required=True, help="包含 operation 和 payload 的管理变更 JSON"
        )
        if action == "access-apply":
            command.add_argument(
                "--confirm-digest", required=True, help="用户确认后使用服务端预览摘要"
            )
    sub.add_parser("account-capabilities", help="查询账号管理和设密邮件能力")
    account_search = sub.add_parser("account-search", help="查找可关联登录账号")
    account_search.add_argument("q")
    for name in ("employee-account", "account-operation"):
        lookup = sub.add_parser(name)
        lookup.add_argument("identifier")
    for name in ("account-preview", "account-submit"):
        command = sub.add_parser(name, help="预览或确认账号操作；不输出密码")
        command.add_argument("--file", required=True, help="账号命令JSON，禁止包含密码")
        if name == "account-submit":
            command.add_argument("--confirm-digest", required=True)
            command.add_argument("--idempotency-key", required=True)
    preview = sub.add_parser("preview")
    preview.add_argument("--file", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument(
        "--file", required=True, help="包含项目、结构版本、字段值和幂等键的 JSON 文件"
    )
    submit.add_argument("--confirm-digest", required=True, help="人工核对 preview 后确认的内容摘要")
    configure_help(parser, sub)
    return parser


def main():
    configure_cli_output()
    parser = build_parser()
    args = parser.parse_args()
    if args.config:
        try:
            connection = read_json_file(args.config)
        except ClientError:
            parser.error("连接配置须为可读取的UTF-8 JSON对象")
        if (
            set(connection) != {"service", "allow_lan_http_demo"}
            or type(connection["allow_lan_http_demo"]) is not bool
            or not isinstance(connection["service"], str)
        ):
            parser.error("连接配置无效")
        if args.service and args.service != connection["service"]:
            parser.error("连接配置与显式服务地址冲突")
        if args.lan_http_demo and not connection["allow_lan_http_demo"]:
            parser.error("显式演示模式与连接配置冲突")
        args.service = connection["service"]
        args.lan_http_demo = connection["allow_lan_http_demo"]

    if args.action is None:
        parser.print_help()
        return
    if args.action == "agent":
        if args.service or args.config or args.lan_http_demo:
            parser.error("Agent使用--agent-config中的服务与模型配置，不混用普通连接参数")
        try:
            agent_main = import_module("office_agent.cli").main
        except ImportError:
            parser.exit(2, "尚未安装可选Agent包；请按clients/agent/README.md安装，不会自动下载。\n")
        raise SystemExit(agent_main(["--config", args.agent_config, "--prompt", args.prompt]))
    if args.action == "login" and not 30 <= args.timeout <= 600:
        parser.error("登录等待时间必须为30–600秒")
    if not args.service and args.action != "doctor":
        parser.error(
            "尚未配置服务端地址，请让AI询问企业地址并读取交付连接配置；不会默认连接localhost。可使用--config或--service。"
        )
    if args.lan_http_demo:
        print("局域网HTTP验收模式：传输未加密，请勿用于正式业务。", file=sys.stderr)
    try:
        if args.action == "doctor":
            result = diagnose(args.service, allow_lan_http_demo=args.lan_http_demo)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result["ok"]:
                parser.exit(1)
            return
        if args.action == "connection-check":
            try:
                result = check_connection(args.service, allow_lan_http_demo=args.lan_http_demo)
            except ConnectionCheckError as error:
                print(json.dumps(error.result(args.service), ensure_ascii=False, indent=2))
                parser.exit(1)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        prepared = None
        if args.action in {"access-preview", "access-apply", "account-preview", "account-submit"}:
            prepared = read_json_file(args.file)
        if args.action in {"preview", "submit"}:
            prepared = read_json_file(args.file)
            digest = file_digest(args.service, prepared)
            if args.action == "preview":
                print(
                    json.dumps(
                        {
                            "service": args.service,
                            "project_id": prepared.get("project_id"),
                            "schema_revision": prepared.get("schema_revision"),
                            "field_ids": sorted(prepared.get("values", {})),
                            "digest": digest,
                            "notice": "请在本机核对完整文件内容后，以该摘要确认提交；未确认不写入",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            if not hmac.compare_digest(digest, args.confirm_digest):
                raise ClientError("提交内容与已确认摘要不一致，请重新预览和确认")
        with OfficeClient(
            args.service, **({"allow_lan_http_demo": True} if args.lan_http_demo else {})
        ) as client:
            handled, result = execute(client, args)
            if handled:
                pass
            elif args.action == "auth-status":
                result = client.auth_status()
            elif args.action == "login":

                def authorize(url):
                    print(
                        json.dumps(
                            {
                                "event": "authorization_pending",
                                "service": client.url,
                                "authorization_url": url,
                                "timeout_seconds": args.timeout,
                                "notice": "请在运行此命令的同一台电脑打开链接；不要粘贴回调URL、授权码或令牌。",
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )

                result = client.login(
                    args.timeout, on_authorize=authorize, open_browser=not args.no_browser
                )
            elif args.action == "logout":
                client.logout()
                result = {"status": "logged_out"}
            elif args.action == "authorizations":
                result = client.client_authorizations(
                    **{
                        key: getattr(args, key)
                        for key in (
                            "scope",
                            "limit",
                            "after",
                            "q",
                            "state",
                            "client_id",
                            "since",
                            "until",
                            "archived",
                            "page",
                            "employee_id",
                            "date_field",
                        )
                    }
                )
            elif args.action == "describe-device":
                result = client.describe_device(args.name, args.platform)
            elif args.action == "authorization-change":
                if not args.confirm:
                    raise ClientError("请先查询授权编号、版本和影响范围；明确确认后加 --confirm")
                result = client.change_authorization(
                    args.identifier, args.version, args.session_action, args.reason
                )
            elif args.action == "schema":
                result = client.schema(args.project_id, args.revision)
            elif args.action == "access-list":
                result = client.access_list(
                    args.kind,
                    q=args.q,
                    after=args.after,
                    limit=args.limit,
                    page=args.page,
                    **{
                        key: getattr(args, "filter_" + key)
                        for key in ("actor", "action", "since", "until")
                    },
                )
            elif args.action == "access-detail":
                result = client.access_detail(
                    args.kind, args.identifier, after=args.after, limit=args.limit
                )
            elif args.action == "access-links":
                result = client.access_links(args.kind, args.identifier, args.relation)
            elif args.action == "access-preview":
                result = client.preview_access_change(prepared)
            elif args.action == "access-apply":
                result = client.apply_access_change(prepared, args.confirm_digest)
            elif args.action == "account-capabilities":
                result = client.account_capabilities()
            elif args.action == "account-search":
                result = client.search_accounts(args.q)
            elif args.action == "employee-account":
                result = client.employee_account(args.identifier)
            elif args.action == "account-operation":
                result = client.account_operation(args.identifier)
            elif args.action == "account-preview":
                result = client.preview_account(prepared)
            elif args.action == "account-submit":
                result = client.submit_account(prepared, args.confirm_digest, args.idempotency_key)
            elif args.action == "submit":
                if prepared is None:
                    raise ClientError("未加载待提交文件")
                result = client.submit(**prepared)
            else:
                result = getattr(client, {"whoami": "me"}.get(args.action, args.action))()
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except ServiceError as error:
        parser.exit(1, error.user_message() + "\n")
    except (ClientError, httpx.HTTPError, OSError, ValueError) as error:
        # HTTP 库异常可能包含 URL；这里仅输出受控错误，不回显响应或令牌。
        parser.exit(
            1,
            (str(error) if isinstance(error, ClientError) else "连接或输入失败，请检查服务和文件")
            + "\n",
        )


if __name__ == "__main__":
    main()
