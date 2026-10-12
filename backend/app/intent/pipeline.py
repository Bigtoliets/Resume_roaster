"""意图层主管道：L1 门卫 → L3 抽取 → 追问判定。

对外的唯一入口是 `resolve`。它**不抛异常、不阻塞**：任何失败都降级成
"没有槽位"，照常交给主循环（Q5）。真正的硬超时在调用方
（`sessions_router._resolve_intent`）再兜一层。

漏斗只有两级：L1 门卫（规则，零成本）→ L3 抽取（小模型）。原设计的 L2 向量
近邻分类已**取消**——它只能给标签、抠不出槽位，在"只决定要不要追问"的定位下
省不下一次 L3 调用，收益抵不上多一个 collection 的维护成本。
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, field_validator

from app.core.config import settings
from app.intent import rules
from app.intent.config import (
    INTENT_TIMEOUT_S,
    SLOT_TARGET_ROLE,
    Intent,
)
from app.intent.extract_prompt import EXTRACT_SYSTEM_PROMPT
from app.llm.client import LLMClient

logger = logging.getLogger(__name__)

_KNOWN_INTENTS = {item.value for item in Intent}

#: 复用的模型客户端。每次新建都要重做一次 TLS 握手，实测把冷调用从
#: 0.6~0.9s 拖到 3.0s（见 config.INTENT_TIMEOUT_S 的说明）。
_LLM: LLMClient | None = None


class _Extraction(BaseModel):
    """L3 的严格输出契约（Q12）。畸形值一律就近修正，不抛给上层。"""

    intent: str = Intent.CHITCHAT.value
    confidence: float = 0.0
    target_role: str | None = None

    @field_validator("intent")
    @classmethod
    def _coerce_intent(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in _KNOWN_INTENTS else Intent.CHITCHAT.value

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        return min(max(number, 0.0), 1.0)

    @field_validator("target_role")
    @classmethod
    def _clean_role(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"null", "none", "无", "没有"}:
            return None
        return text


class TurnIntent(BaseModel):
    """一回合的归一化结果。`ask` 为真时不要进主循环。"""

    model_config = {"frozen": True}

    intent: str = ""
    confidence: float = 0.0
    target_role: str = ""
    source: str = "none"  # rules | llm | none
    ask: bool = False
    slot: str = ""


def _build_llm() -> LLMClient:
    """L3 的模型：不新增配置面，直接用 .env 的默认对话模型（Q17）。

    进程内复用同一个客户端；`.env` 改了要重启才生效，和本项目的其他配置一致。
    """
    global _LLM
    if _LLM is None:
        _LLM = LLMClient(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            model=settings.default_chat_model,
            timeout_s=max(int(INTENT_TIMEOUT_S) + 1, 3),
        )
    return _LLM


async def _extract(text: str) -> _Extraction | None:
    """跑一次 L3。失败返回 None，由调用方降级（Q5）。"""
    try:
        llm = _build_llm()
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        got = await asyncio.wait_for(
            llm.complete_json(messages, _Extraction, name="intent-extract"),
            timeout=INTENT_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 - 任何异常都降级，不阻塞主循环
        logger.info("意图抽取降级（%s）：%s", type(exc).__name__, exc)
        return None
    return got


def _grounded(role: str, text: str) -> str:
    """Q12：岗位必须是原句子串，否则宁可当作没抽到，也不能让模型编一个出来。"""
    if role and role not in text:
        logger.info("意图抽取的 target_role 不在原句中，已丢弃：%r", role)
        return ""
    return role


async def resolve(
    *,
    text: str,
    inherited_role: str = "",
    pending_slot: str = "",
) -> TurnIntent:
    """把这一轮用户输入归一化成槽位 + 是否追问。

    `pending_slot` 非空表示"用户正在回答上一条追问"（Q16）：跳过门卫、
    只抽这个槽位，并且**不再追问**，避免无限往返。
    """
    text = (text or "").strip()
    if not text:
        return TurnIntent()

    # 用户正在回答追问：门卫让路，直接抽槽位
    if pending_slot:
        got = await _extract(text)
        if got is None:
            return TurnIntent(intent=Intent.DIAGNOSE.value, source="none")
        role = _grounded(got.target_role or "", text)
        if pending_slot != SLOT_TARGET_ROLE:
            role = ""
        return TurnIntent(
            intent=Intent.DIAGNOSE.value,
            confidence=got.confidence,
            target_role=role,
            source="llm",
        )

    # L1：能抠到值就短路，整层零成本
    slots = rules.match_slot_rules(text)
    if slots.get(SLOT_TARGET_ROLE):
        return TurnIntent(
            intent=Intent.DIAGNOSE.value,
            confidence=1.0,
            target_role=slots[SLOT_TARGET_ROLE],
            source="rules",
        )

    # L1：只判定主题。没命中就是放行 —— 意图层完全不介入
    if not rules.has_trigger_word(text):
        return TurnIntent()

    got = await _extract(text)
    if got is None:
        return TurnIntent()

    role = _grounded(got.target_role or "", text)
    result = TurnIntent(
        intent=got.intent,
        confidence=got.confidence,
        target_role=role,
        source="llm",
    )
    if got.intent == Intent.DIAGNOSE.value and not role and not inherited_role:
        result = result.model_copy(
            update={"ask": True, "slot": SLOT_TARGET_ROLE}
        )
    return result


__all__ = ["TurnIntent", "resolve"]
