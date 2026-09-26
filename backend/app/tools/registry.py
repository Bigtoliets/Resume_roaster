"""工具注册表与唯一执行入口（D32）。

新增一个工具 = 在本目录加一个 `<impl_key>.py`，用 `@tool(...)` 装饰
`async def run(input, ctx)`，然后在 `app/tools/__init__.py` 里 import 它。
页面上填的 `impl_key` 命中不到这里，就显示「未实现」。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from app.tools._llm_runner import ToolSetupError, prepare_llm
from app.tools.base import ToolContext, ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models import Tool
    from app.llm.client import LLMClient

logger = logging.getLogger(__name__)

TOOLS: dict[str, ToolSpec] = {}


def tool(
    *,
    impl_key: str,
    title: str,
    description: str,
    params_schema: dict[str, Any] | None = None,
    returns_schema: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    kind: str = "builtin",
    needs_prompt: bool | None = None,
    default_timeout_s: int = 120,
) -> Any:
    """注册一个工具实现。三个 schema 都是"新建工具时带出的默认值"，页面可随意改。"""

    def wrapper(runner: Any) -> Any:
        if impl_key in TOOLS:
            raise RuntimeError(f"工具 impl_key 重复注册：{impl_key}")
        TOOLS[impl_key] = ToolSpec(
            impl_key=impl_key,
            title=title,
            description=description,
            kind=kind,
            default_params_schema=params_schema or {"type": "object", "properties": {}},
            default_returns_schema=returns_schema or {"type": "object"},
            default_config=config or {},
            runner=runner,
            needs_prompt=bool(kind == "llm") if needs_prompt is None else needs_prompt,
            default_timeout_s=default_timeout_s,
        )
        return runner

    return wrapper


def get_impl(impl_key: str | None) -> ToolSpec | None:
    return TOOLS.get(impl_key or "")


def impl_status(tool: Tool) -> str:
    """`ok` 已实现 / `missing` 代码里没有 / `unset` 页面没选实现。"""
    if not (tool.impl_key or "").strip():
        return "unset"
    return "ok" if tool.impl_key in TOOLS else "missing"


def list_impls() -> list[dict[str, Any]]:
    """给工具页的下拉与「新建时带出默认值」用。"""
    return [
        {
            "impl_key": spec.impl_key,
            "title": spec.title,
            "description": spec.description,
            "kind": spec.kind,
            "needs_prompt": spec.needs_prompt,
            "params_schema": spec.default_params_schema,
            "returns_schema": spec.default_returns_schema,
            "config": spec.default_config,
            "default_timeout_s": spec.default_timeout_s,
        }
        for spec in sorted(TOOLS.values(), key=lambda item: item.impl_key)
    ]


def build_tool_schema(tool: Tool) -> dict[str, Any]:
    """给模型看的函数定义：参数 schema 以**页面**那份为准。"""
    spec = get_impl(tool.impl_key)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": ((tool.description or "").strip() or (spec.description if spec else ""))[
                :1024
            ],
            "parameters": tool.params_schema or {"type": "object", "properties": {}},
        },
    }


def _fail(summary: str, error: str) -> dict[str, Any]:
    return {"ok": False, "summary": summary, "error": error}


def _schema_properties(tool: Tool) -> dict[str, Any]:
    return (tool.params_schema or {}).get("properties") or {}


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    """尝试修复「被输出长度截断」的 JSON 参数对象。

    LLM 输出超长时的典型形态：字符串写到一半就断了（`json.loads` 报 Unterminated string）。
    这里退回到"最后一个完整的键值对"（字符串之外、顶层对象内的那个逗号）再补右花括号，
    只做这一种保守修复——猜多了比报错更危险。
    """
    if not text.lstrip().startswith("{"):
        return None
    depth = 0
    in_string = False
    escaped = False
    last_safe_comma = -1
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == "," and depth == 1:
            last_safe_comma = index

    if last_safe_comma <= 0:
        return None
    try:
        data = json.loads(text[:last_safe_comma].rstrip() + "}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def normalize_arguments(tool: Tool, arguments: Any) -> tuple[dict[str, Any], str]:
    """把模型给的工具参数整理成扁平 dict，返回 (参数, 错误说明)。

    要处理三种真实发生过的坏形状：
      1. `{"_raw": "{\\"a\\": 1, ..."}` —— 参数是字符串（太长的 JSON 被截断或转义失败）
      2. `{"input": {...}}`               —— 模型按"文本协议"多包了一层
      3. 必填字段缺失                      —— 直接给出可执行的错误，别让下游报"缺少模板变量"
    第二种针对的是那些被提示词教成 `{"tool":..., "input":{...}}` 的模型：
    只要 schema 里没定义 `input` 这个字段，就认定它是信封并拆开。
    """
    args = dict(arguments) if isinstance(arguments, dict) else {}

    if set(args) == {"_raw"} and isinstance(args["_raw"], str):
        text = args["_raw"]
        parsed: dict[str, Any] | None = None
        try:
            loaded = json.loads(text)
            parsed = loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            parsed = _repair_truncated_json(text)
        if parsed is None:
            return {}, (
                "工具参数不是合法 JSON，且无法修复——最常见的原因是参数内容太长被输出截断。"
                "请把参数压缩后重新调用：简历只保留与目标岗位相关的技能、项目、实习（控制在 1500 字内），"
                "不要让整篇原文进参数。"
            )
        args = parsed

    properties = _schema_properties(tool)
    if "input" in args and "input" not in properties and isinstance(args["input"], dict):
        args = dict(args["input"])

    required = [name for name in ((tool.params_schema or {}).get("required") or []) if name]
    missing = [name for name in required if name not in args]
    if missing:
        got = "、".join(list(args)[:8]) or "（空）"
        return {}, (
            f"缺少必填参数：{'、'.join(missing)}。实际收到的参数只有：{got}。"
            "若刚才是因为内容太长被截断，请缩短参数后重试。"
        )
    return args, ""


def _normalize(result: Any) -> dict[str, Any]:
    """返回值统一成 {ok, summary, data}，summary 是给模型看的观察结果。"""
    if not isinstance(result, dict):
        return {"ok": True, "summary": str(result)[:500], "data": result}
    result.setdefault("ok", True)
    result.setdefault("summary", "")
    if "data" not in result:
        # 工具直接返回了业务字段：搬进 data，顶层只留 ok / summary / error / data
        keep = {key: result[key] for key in ("ok", "summary", "error") if key in result}
        keep["data"] = {
            key: value for key, value in result.items() if key not in {"ok", "summary", "error"}
        }
        result = keep
    return result


async def execute_tool(
    db: AsyncSession,
    *,
    tool: Tool,
    arguments: dict[str, Any] | None,
    fallback_llm: LLMClient | None = None,
) -> dict[str, Any]:
    """会话与测试场共用的唯一执行入口。任何失败都返回观察结果，不抛异常。

    外面包一层 `as_type="tool"` 的 observation：Langfuse v4 会把它渲染成工具调用，
    一眼能看到"调了哪个工具、传了什么参数、拿回什么结果"，而不用去 generation 的
    output JSON 里翻。观测失败静默降级，不影响执行。
    """
    from app.obs.langfuse import observation as trace_span

    with trace_span(
        f"tool:{tool.name}",
        as_type="tool",
        input={
            "tool": tool.display_name or tool.name,
            "impl_key": tool.impl_key,
            "arguments": arguments or {},
        },
    ) as span:
        result = await _execute(db, tool=tool, arguments=arguments, fallback_llm=fallback_llm)
        if span is not None:
            with contextlib.suppress(Exception):
                span.update(output=result)
    return result


async def _execute(
    db: AsyncSession,
    *,
    tool: Tool,
    arguments: dict[str, Any] | None,
    fallback_llm: LLMClient | None = None,
) -> dict[str, Any]:
    spec = get_impl(tool.impl_key)
    if spec is None:
        return _fail(
            f"后端还没有实现「{tool.impl_key or '未指定 impl_key'}」，"
            "请在 app/tools/ 下添加对应模块，或换一个已实现的工具",
            "not_implemented",
        )

    args, args_error = normalize_arguments(tool, arguments)
    if args_error:
        return _fail(args_error, "bad_arguments")
    config = dict(tool.impl_config or {})
    ctx = ToolContext(tool=tool, config=config, db=db)

    if spec.needs_prompt:
        try:
            ctx.llm, ctx.system, ctx.user = await prepare_llm(
                db, tool=tool, variables={**config, **args}, fallback_llm=fallback_llm
            )
        except ToolSetupError as exc:
            return _fail(str(exc), "tool_not_ready")

    payload = {"args": args, "config": config}
    timeout_s = int(tool.timeout_s or 0)
    try:
        if timeout_s > 0:
            # 页面上配的「工具内超时」在这里真正生效：跑飞了也不会拖着整个回合
            result = await asyncio.wait_for(spec.runner(payload, ctx), timeout=timeout_s)
        else:
            result = await spec.runner(payload, ctx)
    except TimeoutError:
        logger.warning("工具 %s(%s) 超时（%ss）", tool.name, spec.impl_key, timeout_s)
        return _fail(f"工具执行超时（{timeout_s} 秒），请缩小范围或换一种查法", "timeout")
    except Exception as exc:  # noqa: BLE001 - 工具失败不能炸掉整个回合
        logger.exception("工具 %s(%s) 执行失败", tool.name, spec.impl_key)
        return _fail(f"{type(exc).__name__}: {exc}", "tool_failed")

    return _normalize(result)
