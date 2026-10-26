"""CLI中文帮助展示；不修改命令语义、认证或JSON结果。"""

import argparse
import shutil
import unicodedata

# 与实际解析器核对完整性，新增命令必须同时给出所属分组和中文说明。
COMMAND_GROUPS = (
    (
        "服务器智能助手",
        (
            ("assistant-conversation-create", "创建本人工作会话"),
            ("assistant-conversations", "分页查看活动或归档会话"),
            ("assistant-conversation", "读取会话和有界历史"),
            ("assistant-conversation-archive", "归档或恢复会话，保留执行证据"),
            ("assistant-capabilities", "查看服务器AI配置与可用状态"),
            ("assistant-start", "启动一次服务器任务，保留原请求标识"),
            ("assistant-runs", "查询本人任务历史与执行状态"),
            ("assistant-run", "核对任务回执及待批预览"),
            ("assistant-decide", "人工核对后批准或拒绝固定提案"),
            ("assistant-cancel", "请求停止任务，已有写入不撤回"),
        ),
    ),
    (
        "开始与诊断",
        (
            ("connection-check", "匿名检查服务和身份入口，不读取凭证"),
            ("doctor", "检查本机环境，不联网或读取凭证"),
            ("agent", "运行可选Agent，模型和服务使用专用配置"),
            ("login", "在同机浏览器中登录授权"),
            ("whoami", "查看当前员工与权限"),
            ("auth-status", "查看本机授权状态，不联网续期"),
            ("capabilities", "查看服务端支持的能力"),
            ("logout", "退出当前服务授权并清理本机凭证"),
        ),
    ),
    (
        "项目与查询",
        (
            ("projects", "查看项目列表"),
            ("schema", "查看项目字段及指定结构版本"),
            ("records", "查看记录列表"),
            ("record", "查看记录当前工作版本"),
            ("record-confirmed", "查看记录最新确认版本"),
            ("record-history", "查看记录版本历史"),
            ("aggregate", "按字段与条件执行受控聚合"),
            ("export-confirmed", "有界导出单项目已确认记录"),
        ),
    ),
    (
        "数据预览与提交",
        (
            ("project-preview", "预览创建项目"),
            ("data-preview", "预览数据管理变更"),
            ("data-apply", "按已确认摘要执行数据变更"),
            ("preview", "在本机预览上报文件及内容摘要"),
            ("submit", "提交已核对的候选数据"),
            ("record-confirm", "按已确认摘要确认记录"),
            ("record-correct", "为已确认记录创建更正版本"),
        ),
    ),
    (
        "人员与权限",
        (
            ("access-list", "查询人员、角色、权限集或审计"),
            ("access-detail", "查看人员、角色或权限集详情"),
            ("access-links", "查看角色及权限的直接关联"),
            ("access-preview", "预览人员与权限变更"),
            ("access-apply", "按已确认摘要执行权限变更"),
            ("pending-identities", "查看待认领的登录身份"),
        ),
    ),
    (
        "登录账号管理",
        (
            ("account-capabilities", "查看账号开通与设密支持情况"),
            ("account-search", "查找已有登录账号"),
            ("employee-account", "查看员工关联的登录账号"),
            ("account-preview", "预览账号操作"),
            ("account-submit", "提交已确认的账号操作"),
            ("account-operation", "查看账号操作结果"),
            ("account-history", "查看账号操作历史"),
            ("account-resume-preview", "预览继续处理未完成的账号操作"),
            ("account-resume", "按已确认预览继续账号操作"),
        ),
    ),
    (
        "客户端授权管理",
        (
            ("authorizations", "查询本人或获准范围内的授权"),
            ("describe-device", "为当前授权登记设备备注"),
            ("authorization-change", "确认后停用或归档授权"),
        ),
    ),
)


def display_width(text):
    return sum(
        0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1
        for c in text
    )


def wrap_text(text, width):
    """按显示列宽折行，避免中文被当作半角而挤出窄终端。"""
    line = ""
    for char in text:
        if line and display_width(line + char) > width:
            yield line
            line = ""
        line += char
    if line:
        yield line


class ChineseFormatter(argparse.HelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, prefix=prefix or "用法：")


class ConsoleParser(argparse.ArgumentParser):
    command_groups = None

    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        kwargs.setdefault("formatter_class", ChineseFormatter)
        super().__init__(*args, **kwargs)
        self.add_argument("-h", "--help", action="help", help="显示帮助并退出")
        self._positionals.title = "参数"
        self._optionals.title = "选项"

    def format_help(self):
        if self.command_groups is None:
            return super().format_help()
        width = max(40, min(110, shutil.get_terminal_size((100, 24)).columns))
        lines = [f"用法：{self.prog} [全局选项] <命令> [命令选项]", "", self.description, ""]
        lines += [
            "常用示例",
            f"  {self.prog} doctor",
            f"  {self.prog} --config 连接.json login",
            f"  {self.prog} --config 连接.json whoami",
            f"  {self.prog} --config 连接.json projects",
            "",
        ]
        column = max(len(name) for _, items in self.command_groups for name, _ in items) + 4
        for title, items in self.command_groups:
            lines.append(title)
            for name, description in items:
                if width < column + 24:
                    lines.append("  " + name)
                    lines.extend("    " + part for part in wrap_text(description, width - 4))
                else:
                    parts = list(wrap_text(description, width - column - 2))
                    lines.append("  " + name.ljust(column) + parts[0])
                    lines.extend(" " * (column + 2) + part for part in parts[1:])
            lines.append("")
        formatter = self._get_formatter()
        formatter.start_section("全局选项")
        formatter.add_arguments(
            [a for a in self._actions if not isinstance(a, argparse._SubParsersAction)]
        )
        formatter.end_section()
        lines += [
            formatter.format_help().rstrip(),
            "",
            f"使用 {self.prog} <命令> -h 查看具体参数；全局选项放在命令之前。",
        ]
        return (
            "\n".join(part for line in lines for part in (list(wrap_text(line, width)) or [""]))
            + "\n"
        )


def configure_help(parser, subparsers):
    declared = [name for _, items in COMMAND_GROUPS for name, _ in items]
    if len(declared) != len(set(declared)) or set(declared) != set(subparsers.choices):
        raise ValueError("命令帮助分组与实际解析器不一致")
    parser.command_groups = COMMAND_GROUPS
    for _, items in COMMAND_GROUPS:
        for name, description in items:
            subparsers.choices[name].description = description
