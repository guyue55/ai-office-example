"""只读模型目录；使用相同SDK与受控出口，不调用推理或猜测模型能力。"""

import asyncio
import time

from app.application.model_test_errors import failure


async def discover_models(config, runner):
    started = time.monotonic()
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    truncated = False
    try:
        async with asyncio.timeout(min(15, config.request_timeout)):
            async with runner.model_context(config) as model:
                page = await model.client.models.list(
                    **({"limit": 100} if config.api == "anthropic-messages" else {})
                )
                for index in range(10):
                    for row in page.data:
                        identifier = getattr(row, "id", None)
                        if (
                            not isinstance(identifier, str)
                            or not identifier
                            or len(identifier) > 200
                            or any(ord(c) < 32 for c in identifier)
                        ):
                            truncated = True
                            continue
                        if identifier in seen:
                            continue
                        if len(items) >= 1000:
                            truncated = True
                            break
                        seen.add(identifier)
                        name = getattr(row, "display_name", None)
                        items.append(
                            {
                                "id": identifier,
                                "name": name[:200] if isinstance(name, str) else identifier,
                            }
                        )
                    more = page.has_next_page()
                    if not more or len(items) >= 1000:
                        truncated = truncated or more
                        break
                    if index == 9:
                        truncated = True
                        break
                    page = await page.get_next_page()
        return {
            "passed": True,
            "category": "ok",
            "message": "已获取模型列表；目录可见不代表所选协议调用可用",
            "suggestion": "可选择或手动输入模型后测试连接。",
            "items": items,
            "truncated": truncated,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 -- 供应商错误正文与凭证不能进入响应。
        result = failure(exc)
        if getattr(exc, "status_code", None) in {404, 405}:
            result.update(
                message="此服务未提供兼容的模型列表，可手动填写模型ID",
                suggestion="无需改地址碰运气；手动输入后测试所选模型。",
            )
        return {
            **result,
            "items": items,
            "truncated": bool(items),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
