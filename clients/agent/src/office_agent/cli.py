"""单次任务CLI；终端真人审批，不让模型执行登录、代输密码或批准自身操作。"""

import argparse
import asyncio
import json
import os
import sys
import threading

from office_client import OfficeClient
from office_client.local_io import configure_cli_output
from office_client.product import PRODUCT

from .config import AgentConfig
from .gateway import Proposal
from .policy import AgentError, sanitize
from .providers import configured_model
from .runtime import run_task


async def terminal_approval(proposal: Proposal) -> bool:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        return False
    print("\n请核对本次变更（未提交）：", file=sys.stderr)
    print(
        json.dumps(
            {
                "operation": proposal.operation,
                "arguments": sanitize(proposal.arguments),
                "preview": proposal.preview,
            },
            ensure_ascii=False,
            indent=2,
        ),
        file=sys.stderr,
    )
    print("仅输入 确认 执行；其他输入均拒绝：", end="", flush=True, file=sys.stderr)
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def finish(value):
        if not future.done():
            future.set_result(value)

    def read():
        try:
            value = sys.stdin.readline().strip() == "确认"
        except (OSError, ValueError):
            value = False
        try:
            loop.call_soon_threadsafe(finish, value)
        except RuntimeError:
            pass  # 任务已取消，不能继续写入。

    # 使用daemon读终端，超时后不会卡住asyncio默认线程池退出；本命令只执行一个任务。
    threading.Thread(target=read, daemon=True, name="office-agent-confirm").start()
    return await future


async def execute(config: AgentConfig, prompt: str):
    async with configured_model(config) as model:
        return await run_task(
            model,
            lambda: OfficeClient(
                config.service_url, allow_lan_http_demo=config.allow_lan_http_demo
            ),
            prompt,
            limits=config.limits,
            approve=terminal_approval,
        )


def main(argv=None):
    configure_cli_output()
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    parser = argparse.ArgumentParser(
        prog=PRODUCT["command"] + " agent", description=PRODUCT["name"] + " · 独立员工端Agent"
    )
    parser.add_argument("--config", required=True, help="可信Agent TOML配置，不接受密钥明文")
    parser.add_argument(
        "--prompt", required=True, help="单次任务；命令历史可能记录文本，请勿输入秘密"
    )
    args = parser.parse_args(argv)
    try:
        config = AgentConfig.load(args.config)
        result = asyncio.run(execute(config, args.prompt))
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0 if result.status == "completed" else 1
    except KeyboardInterrupt:
        print("任务已取消；在途写入可能已经生效，请核对服务端再操作。", file=sys.stderr)
        return 130
    except AgentError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 -- CLI最终错误边界不展示第三方异常正文。
        print("Agent启动失败，请核对模型、配置和授权；未输出异常正文。", file=sys.stderr)
        return 1
