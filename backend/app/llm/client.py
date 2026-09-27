"""LLM 网关：OpenAI 兼容协议，无厂商特判；JSON 强校验 + 重试 + 宽松兜底。"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCallRequest:
    """模型要求调用的一次工具。"""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass
class ChatStepResult:
    """一轮模型调用的结果：要么是最终文本，要么是若干工具调用。"""

    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = ""
    #: 这一轮真实喂进去的 prompt token 数（服务端算的，不是估算）。
    #: 长上下文治理的换页判断就靠它（见 app/agent/notes.py 与 loop 的换页逻辑）。
    prompt_tokens: int = 0
    #: 输出 token 数，用于算"上一轮的增量"，从而预测下一轮会不会顶到窗口上限。
    completion_tokens: int = 0


class LLMClient:
    """一次可用的模型调用封装（对应一套模型配置）。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        params: dict[str, Any] | None = None,
        timeout_s: int = 120,
    ) -> None:
        if not api_key:
            raise LLMError(f"模型 {model} 缺少 API Key，请在「模型配置」页填写")
        self.model = model
        self.params = dict(params or {})
        self._client = _build_client(base_url, api_key, timeout_s)

    # ---------- 基础调用 ----------

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        name: str = "llm-call",
    ) -> str:
        """非流式调用。

        `name` 只是 Langfuse 里那条 generation 的名字。默认 `llm-call` 是"主循环之外
        的辅助调用"，真正有语义的调用方（比如笔记投影）会传自己的名字，
        这样 trace 上一眼能分出"哪次是模型在思考、哪次是系统在做记忆换页"。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **self.params,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        with _generation(name, model=self.model, input_value=messages) as generation:
            try:
                resp = await self._client.chat.completions.create(**payload)
            except (APITimeoutError, RateLimitError, APIError) as exc:
                raise LLMError(f"模型调用失败（{self.model}）：{exc}") from exc
            text = resp.choices[0].message.content or ""
            if generation is not None:
                with contextlib.suppress(Exception):
                    generation.update(
                        output={"role": "assistant", "content": text},
                        usage_details=_usage_details(getattr(resp, "usage", None)) or None,
                    )
            return text

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.params,
        }
        try:
            stream = await self._client.chat.completions.create(**payload)
            async for chunk in stream:  # type: ignore[union-attr]
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except (APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(f"模型流式调用失败（{self.model}）：{exc}") from exc

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        new_from: int | None = None,
    ) -> ChatStepResult:
        """一轮 ReAct 调用：流式产出文本，同时累积模型发起的工具调用。

        模型要么回文本（最终回答），要么回一个/多个 tool_calls（下一步要执行的工具）。

        `new_from` 是"本轮新增消息的起始下标"：只影响上报给 Langfuse 的 input
        （只记增量，trace 里读起来清爽），不影响真正发给模型的 messages。
        """
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        payload.update({k: v for k, v in self.params.items() if k not in {"stream", "tools"}})
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # 让流式响应带上 token 用量（手动上报 generation 时要用）
        payload["stream_options"] = {"include_usage": True}

        start = new_from if isinstance(new_from, int) and 0 <= new_from <= len(messages) else 0
        shown_messages = messages[start:]
        content_parts: list[str] = []
        slots: dict[int, dict[str, str]] = {}
        finish_reason = ""
        usage: Any = None

        with _generation("llm-call", model=self.model, input_value=shown_messages) as generation:
            try:
                stream = await self._client.chat.completions.create(**payload)
                async for chunk in stream:  # type: ignore[union-attr]
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    text = getattr(delta, "content", None)
                    if text:
                        content_parts.append(text)
                        if on_token is not None:
                            await on_token(text)
                    for call in getattr(delta, "tool_calls", None) or []:
                        slot = slots.setdefault(
                            call.index or 0, {"id": "", "name": "", "arguments": ""}
                        )
                        if getattr(call, "id", None):
                            slot["id"] = call.id
                        function = getattr(call, "function", None)
                        if function is not None:
                            if getattr(function, "name", None):
                                slot["name"] = function.name
                            if getattr(function, "arguments", None):
                                slot["arguments"] += function.arguments
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
            except (APITimeoutError, RateLimitError, APIError) as exc:
                raise LLMError(f"模型调用失败（{self.model}）：{exc}") from exc

            requests: list[ToolCallRequest] = []
            for index in sorted(slots):
                slot = slots[index]
                if not slot["name"]:
                    continue
                raw = slot["arguments"] or "{}"
                try:
                    arguments = _loads_lenient(raw)
                except ValueError:
                    arguments = {"_raw": raw}
                requests.append(
                    ToolCallRequest(
                        id=slot["id"] or f"call_{index}",
                        name=slot["name"],
                        arguments=arguments,
                        raw_arguments=raw,
                    )
                )
            result = ChatStepResult(
                content="".join(content_parts).strip(),
                tool_calls=requests,
                finish_reason=finish_reason,
                # 带上真实用量：换页判断要用 prompt_tokens，
                # "上一轮增量"用 prompt + completion 之和来外推。
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            )
            if generation is not None:
                with contextlib.suppress(Exception):
                    generation.update(
                        output=_assistant_message(result),
                        usage_details=_usage_details(usage) or None,
                        metadata={
                            "requested_model": self.model,
                            "finish_reason": result.finish_reason,
                            # input 只展示增量，这里给出完整上下文的规模
                            "context_messages": len(messages),
                            "context_chars": sum(
                                len(str(item.get("content") or "")) for item in messages
                            ),
                            "new_messages": len(shown_messages),
                        },
                    )
        return result

    # ---------- 结构化输出 ----------

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: type[TModel],
        *,
        name: str = "llm-call",
    ) -> TModel:
        """要求模型输出 JSON 并强校验；失败则追加一句重试一次。"""
        attempt_messages = list(messages)
        last_error: Exception | None = None
        for attempt in range(2):
            raw = await self.complete(attempt_messages, json_mode=True, name=name)
            try:
                return schema.model_validate(_loads_lenient(raw))
            except (ValidationError, ValueError) as exc:
                last_error = exc
                logger.warning("结构化输出校验失败（第 %s 次）：%s", attempt + 1, exc)
                attempt_messages = [
                    *messages,
                    {"role": "assistant", "content": raw[:2000]},
                    {
                        "role": "user",
                        "content": _retry_instruction(schema),
                    },
                ]
        raise LLMError(f"模型未能输出合法结构化结果：{last_error}")


def _retry_instruction(schema: type[BaseModel]) -> str:
    schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)[:1500]
    return f"上面的输出不是合法 JSON 或不符合约定结构。请只输出符合该结构的 JSON：{schema_text}"


def _assistant_message(result: ChatStepResult) -> dict[str, Any]:
    """按 Langfuse 官方约定构造 generation 的 output。

    必须是一条标准 assistant 消息：`tool_calls` 放在它的 `tool_calls` 数组里、
    每个 call 的 `arguments` 是 **JSON 字符串**（不是对象），否则 UI 不会渲染成工具调用卡片。
    """
    output: dict[str, Any] = {"role": "assistant", "content": result.content or ""}
    if result.tool_calls:
        output["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments
                    or json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in result.tool_calls
        ]
    return output


def _build_client(base_url: str, api_key: str, timeout_s: int):  # type: ignore[no-untyped-def]
    """一律用原生 OpenAI 客户端。

    之前用 `langfuse.openai` 的包装器，它会把**整个请求 payload**（含几十 KB 的 tools 定义）
    当作 input 记录，而且它创建的 generation 是隐式的——在 SSE 这种会切换任务上下文的场景里
    `update_current_generation()` 经常找不到它，覆盖不生效。改成手动建 generation
    （见 `_generation`），input/output/usage 全由我们显式给，稳定可控。
    """
    return AsyncOpenAI(base_url=base_url or None, api_key=api_key, timeout=timeout_s)


@contextmanager
def _generation(name: str, *, model: str, input_value: Any) -> Iterator[Any]:
    """手动开一个 `generation` 观测，返回可显式 update 的句柄。

    Langfuse 未配置时退化为 None，业务代码无需分支。
    """
    from app.obs.langfuse import observation as trace_span

    with trace_span(name, as_type="generation", model=model, input=input_value) as span:
        yield span


def _usage_details(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    return {
        "input": getattr(usage, "prompt_tokens", None),
        "output": getattr(usage, "completion_tokens", None),
        "total": getattr(usage, "total_tokens", None),
    }


def _loads_lenient(raw: str) -> dict[str, Any]:
    """宽松解析：剥离代码围栏，截取首个 { 到末个 }。"""
    text = _FENCE_RE.sub("", (raw or "").strip()).strip()
    if not text:
        raise ValueError("模型返回空内容")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"未找到 JSON 主体：{text[:200]}") from None
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型输出的 JSON 顶层不是对象")
    return data
