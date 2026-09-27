"""有模型工具的共享辅助：把页面配置变成"可直接用的客户端 + 渲染好的提示词"。

工具函数不该关心密钥解密、参数合并、超时这些事，所以统一在这里做一次。
命名带下划线：它不是工具，不会被注册进工具清单。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import APIError, APITimeoutError, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt
from app.db.models import ModelConfig, Prompt, Tool
from app.llm.client import LLMClient

logger = logging.getLogger(__name__)

#: 重试次数的安全上限（页面上的「失败重试」填再大也不会超过这个数）
MAX_RETRY_TIMES = 3
RETRY_BACKOFF_S = 1.5


class ToolSetupError(Exception):
    """配置不完整（缺 Prompt / 缺模型 / 模板变量没值），不是运行期故障。"""


def pick_message(messages: list[dict[str, Any]] | None, role: str) -> str:
    return next(
        (
            str(message.get("content"))
            for message in (messages or [])
            if message.get("role") == role and message.get("content")
        ),
        "",
    )


def render(template: str, variables: dict[str, Any]) -> str:
    """把 `{{key}}` 替换成值；非字符串值按 JSON 渲染。"""
    body = template
    for key, value in variables.items():
        token = "{{" + key + "}}"
        if token in body:
            body = body.replace(
                token, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            )
    return body


def build_client(
    model_row: ModelConfig,
    params_override: dict[str, Any],
    timeout_s: int | None = None,
) -> LLMClient:
    return LLMClient(
        base_url=model_row.base_url,
        api_key=decrypt(model_row.api_key_cipher),
        model=model_row.model_name,
        params={**(model_row.default_params or {}), **(params_override or {})},
        # 工具自己的「工具内超时」优先于模型的默认超时（终于不再是死配置）
        timeout_s=int(timeout_s or 0) or model_row.timeout_s,
    )


async def complete_with_prompt(ctx: Any, *, role: str) -> dict[str, Any]:
    """有模型工具的公共尾巴：把渲染好的提示词发一次模型，返回统一 JSON。

    工具模块各自保留 `run(input, ctx)` 函数体（哪怕只有这一行），
    是为了让"这个工具做什么"永远能在代码里一眼看到。

    重试次数取工具配置里的 `retry_times`（页面可改，D36）：一个面试官工具跑 20-30 秒，
    偶发 502 就整轮失败太亏。参数错误（400）不重试，重试也没用。
    """
    if ctx.llm is None:
        return {"ok": False, "summary": "这个工具没有可用模型", "error": "missing_model"}

    retries = min(max(int(getattr(ctx.tool, "retry_times", 0) or 0), 0), MAX_RETRY_TIMES)
    messages = [
        {"role": "system", "content": ctx.system},
        {"role": "user", "content": ctx.user},
    ]
    text = ""
    for attempt in range(retries + 1):
        try:
            text = (await ctx.llm.complete(messages)).strip()
        except (APITimeoutError, RateLimitError, APIError) as exc:
            if attempt >= retries:
                logger.warning("工具 %s 调用模型失败（已重试）：%s", ctx.tool.name, exc)
                return {
                    "ok": False,
                    "summary": f"模型调用失败：{exc}",
                    "error": "llm_failed",
                }
            logger.info(
                "工具 %s 第 %s 次调用失败，%.1fs 后重试",
                ctx.tool.name,
                attempt + 1,
                RETRY_BACKOFF_S,
            )
            await asyncio.sleep(RETRY_BACKOFF_S * (attempt + 1))
            continue

        if text:
            break

        # 空输出同样要重试。实测踩过：`final_judge` 面对"简历 + JD + 两份面试报告"这种长输入时
        # 直接返回空（reasoning 模型的思维链把输出预算吃光，或上游抖一下），
        # 整个回合就断在最后一步、报告没产出。空输出大多是瞬时的，重试一次远比直接判失败划算。
        logger.warning(
            "工具 %s 第 %s 次调用返回空内容%s",
            ctx.tool.name,
            attempt + 1,
            "，即将重试" if attempt < retries else "（重试后仍为空）",
        )
        if attempt < retries:
            await asyncio.sleep(RETRY_BACKOFF_S * (attempt + 1))

    # 摘要结尾必须标出总字数：只写"前 200 字 + …"的话，任何看图的人（包括模型）
    # 都会以为工具只返回了 200 字、内容被截断了。实测把这句话误导进了笔记，
    # 导致主模型重跑面试官工具去找"被截断的后半段"。
    if not text:
        summary = (
            "模型没有返回内容（已重试）。常见原因：入参太长导致输出预算被思维链吃光，"
            "或该模型不支持当前参数。可缩小入参，或换 max_tokens 更大的模型。"
        )
    elif len(text) > 200:
        summary = f"{text[:200]}…（完整输出共 {len(text)} 字，已存入结果，需要时按指针回查）"
    else:
        summary = text
    return {
        "ok": bool(text),
        "summary": summary,
        "data": {"text": text, "role": role},
    }


async def prepare_llm(
    db: AsyncSession,
    *,
    tool: Tool,
    variables: dict[str, Any],
    fallback_llm: LLMClient | None,
) -> tuple[LLMClient, str, str]:
    """按页面配置准备 (客户端, system, user)。缺任何一项都抛 ToolSetupError。"""
    prompt_row = await db.get(Prompt, tool.prompt_id) if tool.prompt_id else None
    if prompt_row is None or not prompt_row.messages:
        raise ToolSetupError(f"工具「{tool.display_name or tool.name}」还没有选择 Prompt")
    if not prompt_row.is_enabled:
        raise ToolSetupError(f"工具「{tool.display_name or tool.name}」选的 Prompt 已停用")

    missing = [name for name in (prompt_row.variables or []) if name not in variables]
    if missing:
        raise ToolSetupError("缺少模板变量：" + "、".join(missing))

    llm = fallback_llm
    if tool.model_config_id:
        model_row = await db.get(ModelConfig, tool.model_config_id)
        if model_row is None:
            raise ToolSetupError(f"工具「{tool.display_name or tool.name}」选的模型不存在")
        llm = build_client(model_row, prompt_row.params or {}, tool.timeout_s)
    if llm is None:
        raise ToolSetupError(f"工具「{tool.display_name or tool.name}」没有可用模型")

    return (
        llm,
        render(pick_message(prompt_row.messages, "system"), variables),
        render(pick_message(prompt_row.messages, "user"), variables),
    )
