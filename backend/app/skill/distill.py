"""蒸馏线路本体（SD6-SD13、SD18-SD20）。

一次蒸馏 = 两轮模型调用（SD11 / SD12）：

    第 1 轮  system: 角色/任务 + 【可用 Skill 目录】(name+description，排除经验 skill)
             -> 模型回 {"load": [...]}
    组装     代码按名字读 backend/skill/<name>/SKILL.md 正文（只读）
    第 2 轮  + 选中正文 + 经验 skill 全文 + 本回合 trace
             -> 模型回：新的 SKILL.md 全文
    校验     格式合法、name 与 description 强制写回、正文 <= 8000 字符（超了带反馈重试 1 次）

三条铁律：
1. **模型不碰文件**，所有 IO 与校验都在这里（SD10）。
2. **任何一步失败都降级**：日志记一笔，文件一字不动（SD19）。
3. 全程走 `asyncio.Lock`，避免两个回合同时改写同一个文件（SD18）。

Langfuse：整条链路是一棵 `skill-distill` 树（`as_type="agent"`），下面挂
`trace-fetch` / `load-skills` / `write-skill` 三个 span 和两到三次 generation，
并和主循环一样按 session_id 聚合。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, Message, Run, Session, Skill
from app.db.session import SessionLocal
from app.obs.langfuse import observation, propagated
from app.skill import distill_prompts as prompts
from app.skill.store import (
    BODY_MAX_CHARS,
    EXPERIENCE_SKILL,
    SEED_DESCRIPTION,
    SkillFormatError,
    compose,
    normalize_skill_text,
    parse_frontmatter,
    read_skill,
    read_skill_body,
    seed_skill_text,
    write_skill_text,
)
from app.skill.sync import upsert_one
from app.skill.trace_source import fetch_trace

logger = logging.getLogger(__name__)

#: Langfuse 上这条线路的 tag（方便在 UI 里筛出来）
TRACE_TAG = "skill-distill"

#: 一次最多接受模型点名几个 skill。口径是"满足要求的都要"，这个上限只是防炸；
#: 一旦卡到会打 warning，不静默截断。
MAX_LOADED_SKILLS = 8

#: 单份被加载的 skill 正文上限，避免第 2 轮输入无上限膨胀
MAX_LOADED_BODY_CHARS = 20000

#: 同一个进程里串行化"读 -> 调模型 -> 写盘"（SD18）
_lock = asyncio.Lock()

#: 持住后台任务的引用，否则可能被 GC 掉
_tasks: set[asyncio.Task[None]] = set()


def _update(span: Any, **kwargs: Any) -> None:
    """给 span 补 output/metadata。观测是旁路，任何失败都静默降级。"""
    if span is None:
        return
    with contextlib.suppress(Exception):
        span.update(**kwargs)


# ---------------------------------------------------------------- 提示词素材


async def build_catalog(db: AsyncSession, *, exclude: str = EXPERIENCE_SKILL) -> str:
    """【可用 Skill】目录 = 其他 skill 的 name + description。

    经验 skill **不列进来**：它不是候选，是被改写的对象（SD11）。
    """
    rows = (
        (
            await db.execute(
                select(Skill)
                .where(Skill.deleted_at.is_(None))
                .where(Skill.is_enabled.is_(True))
                .order_by(Skill.name)
            )
        )
        .scalars()
        .all()
    )
    lines = [
        f"- {row.name}: {(row.description or '').strip()}"
        for row in rows
        if row.name != exclude and (row.description or "").strip()
    ]
    return "\n".join(lines) if lines else "（当前没有其他 skill）"


def load_skill_bodies(names: list[str]) -> tuple[str, list[str]]:
    """按名字读正文，返回 `(拼好的正文块, 真正读到的名字)`。

    读不到的写一行说明（对模型是有效反馈），但不抛错。
    """
    if len(names) > MAX_LOADED_SKILLS:
        logger.warning(
            "模型点名了 %s 个 skill，超过上限 %s，只加载前 %s 个：%s",
            len(names),
            MAX_LOADED_SKILLS,
            MAX_LOADED_SKILLS,
            names[MAX_LOADED_SKILLS:],
        )
    chunks: list[str] = []
    loaded: list[str] = []
    for name in names[:MAX_LOADED_SKILLS]:
        body = read_skill_body(name)
        if body is None:
            chunks.append(f"## {name}\n（读不到这份 skill 的正文，可能已不存在）")
            continue
        if len(body) > MAX_LOADED_BODY_CHARS:
            body = body[:MAX_LOADED_BODY_CHARS] + "\n…（正文过长，已截断）"
        chunks.append(f"## {name}\n{body}")
        loaded.append(name)
    return ("\n\n".join(chunks) if chunks else "（本轮不需要其他 skill 正文）"), loaded


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个 JSON 对象。容忍代码围栏与前后废话。"""
    if not text:
        return None
    stripped = text.strip()
    with contextlib.suppress(json.JSONDecodeError):
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    start = stripped.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    with contextlib.suppress(json.JSONDecodeError):
                        data = json.loads(stripped[start : index + 1])
                        if isinstance(data, dict):
                            return data
                    break
        start = stripped.find("{", start + 1)
    return None


def strip_code_fence(text: str) -> str:
    """模型偶尔把整份文件包在 markdown 围栏里，剥掉最外层。"""
    body = (text or "").strip()
    if not body.startswith("```"):
        return body
    lines = body.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def body_length(text: str) -> int:
    with contextlib.suppress(SkillFormatError):
        _, body = parse_frontmatter(text)
        return len(body.strip())
    return len(text.strip())


# ---------------------------------------------------------------- 上下文


async def _load_context(db: AsyncSession, run_id: int) -> tuple[Run, Agent] | None:
    """取这一回合的 run 与它所属的 Agent，并检查自动蒸馏开关（SD22）。"""
    run = await db.get(Run, run_id)
    if run is None:
        logger.info("蒸馏跳过：run#%s 不存在", run_id)
        return None
    session_row = await db.get(Session, run.session_id) if run.session_id else None
    agent = await db.get(Agent, session_row.agent_id) if session_row and session_row.agent_id else None
    if agent is None or agent.deleted_at is not None:
        logger.info("蒸馏跳过：run#%s 找不到可用 Agent", run_id)
        return None
    if not getattr(agent, "auto_distill", False):
        logger.info("蒸馏跳过：Agent #%s 未开启自动蒸馏", agent.id)
        return None
    return run, agent


async def _question_of(db: AsyncSession, run_id: int) -> str:
    """这一回合用户问了什么（放进 trace 的 input，让人一眼看懂）。"""
    row = (
        await db.execute(
            select(Message)
            .where(Message.run_id == run_id)
            .where(Message.role == "user")
            .order_by(Message.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return ""
    return str((row.content or {}).get("text") or "")[:200]


# ---------------------------------------------------------------- 主流程


async def run_distillation(run_id: int) -> bool:
    """跑完一次蒸馏。返回是否成功写盘。**异常都在内部消化**（SD19）。"""
    async with SessionLocal() as db:
        context = await _load_context(db, run_id)
        if context is None:
            return False
        run, agent = context
        question = await _question_of(db, run_id)

        with propagated(session_id=f"session_{run.session_id}", tags=[TRACE_TAG]):
            with observation(
                TRACE_TAG,
                as_type="agent",
                input={"run_id": run_id, "question": question, "skill": EXPERIENCE_SKILL},
                metadata={
                    "session_id": run.session_id,
                    "run_status": run.status,
                    "source_trace_id": run.langfuse_trace_id,
                    "agent": agent.name,
                    "agent_id": agent.id,
                },
            ) as root:
                ok, summary = await _distill(db, run_id, run, agent)
                _update(root, output=summary)
                return ok


async def _distill(
    db: AsyncSession, run_id: int, run: Run, agent: Agent
) -> tuple[bool, dict[str, Any]]:
    """真正的两轮流程。返回 `(是否写盘成功, 给 trace 的摘要)`。"""
    from app.agent.loop import build_agent_llm  # 延迟导入，避免循环依赖

    # ── 取轨迹 ──────────────────────────────────────────────────────────
    with observation(
        "trace-fetch", as_type="span", input={"trace_id": run.langfuse_trace_id}
    ) as fetch_span:
        trace = await fetch_trace(run.langfuse_trace_id or "")
        _update(
            fetch_span,
            output={"observations": len(trace) if trace else 0, "fetched": bool(trace)},
        )
    if not trace:
        logger.info("蒸馏跳过：run#%s 取不到轨迹（trace=%s）", run_id, run.langfuse_trace_id)
        return False, {"updated": False, "reason": "no-trace"}

    trace_text = json.dumps(trace, ensure_ascii=False, indent=2)
    catalog = await build_catalog(db)
    llm = await build_agent_llm(db, agent)
    system = {"role": "system", "content": prompts.render(prompts.ROUND1, CATALOG=catalog)}

    # ── 第 1 轮：只有 system，问它要哪些 skill 正文 ──────────────────────
    try:
        round1_text = str(await llm.complete([system], name="skill-distill-round1") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("蒸馏第 1 轮调用失败（run#%s）：%s", run_id, exc)
        return False, {"updated": False, "reason": "round1-failed", "error": str(exc)[:200]}

    wanted = extract_json_object(round1_text) or {}
    names = [str(name) for name in (wanted.get("load") or []) if isinstance(name, str)]
    logger.info("蒸馏 run#%s 第 1 轮点名了 %s 个 skill：%s", run_id, len(names), names)

    # ── 组装被点名的正文 ────────────────────────────────────────────────
    with observation("load-skills", as_type="span", input={"requested": names}) as load_span:
        loaded_text, loaded_names = load_skill_bodies(names)
        _update(
            load_span,
            output={
                "loaded": loaded_names,
                "missing": [n for n in names if n not in loaded_names],
                "chars": len(loaded_text),
            },
        )

    # ── 第 2 轮：正文 + 经验 skill + trace → 整份新文件 ──────────────────
    current = read_skill(EXPERIENCE_SKILL)
    current_text = compose(current.meta, current.body) if current else seed_skill_text()
    # description 锁在当前文件里的那一份（模型改不动；人改文件即可换）
    locked_description = current.description if current else SEED_DESCRIPTION

    user_content = prompts.render(
        prompts.ROUND2_USER,
        LOADED=loaded_text,
        SKILL_NAME=EXPERIENCE_SKILL,
        EXPERIENCE=current_text,
        TRACE=trace_text,
    )
    messages = [system, {"role": "user", "content": user_content}]
    try:
        output = strip_code_fence(
            str(await llm.complete(messages, name="skill-distill-round2") or "")
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("蒸馏第 2 轮调用失败（run#%s）：%s", run_id, exc)
        return False, {"updated": False, "reason": "round2-failed", "error": str(exc)[:200]}

    # ── 校验；超长则带反馈重试一次（SD20）──────────────────────────────
    text = ""
    try:
        text = normalize_skill_text(
            output, name=EXPERIENCE_SKILL, force_description=locked_description
        )
    except SkillFormatError as exc:
        if "正文过长" not in str(exc):
            logger.warning("蒸馏产出不合法，放弃本次（run#%s）：%s", run_id, exc)
            return False, {"updated": False, "reason": "invalid", "error": str(exc)[:200]}
        retry_messages = messages + [
            {"role": "assistant", "content": output},
            {
                "role": "user",
                "content": prompts.render(
                    prompts.TOO_LONG_FEEDBACK,
                    ACTUAL=body_length(output),
                    LIMIT=BODY_MAX_CHARS,
                ),
            },
        ]
        try:
            retried = strip_code_fence(
                str(await llm.complete(retry_messages, name="skill-distill-compress") or "")
            )
            text = normalize_skill_text(
                retried, name=EXPERIENCE_SKILL, force_description=locked_description
            )
        except Exception as retry_exc:  # noqa: BLE001
            logger.warning(
                "蒸馏产出超长且压缩后仍不合法，放弃本次（run#%s）：%s", run_id, retry_exc
            )
            return False, {"updated": False, "reason": "still-too-long"}

    # ── 落盘 + 写路径 upsert（SD17）────────────────────────────────────
    with observation(
        "write-skill",
        as_type="span",
        input={"file": f"backend/skill/{EXPERIENCE_SKILL}/SKILL.md"},
    ) as write_span:
        try:
            path = write_skill_text(EXPERIENCE_SKILL, text)
            refreshed = read_skill(EXPERIENCE_SKILL)
            if refreshed is not None:
                await upsert_one(db, refreshed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("蒸馏写盘失败（run#%s）：%s", run_id, exc)
            _update(write_span, output={"ok": False, "error": str(exc)[:200]})
            return False, {"updated": False, "reason": "write-failed", "error": str(exc)[:200]}
        _update(write_span, output={"ok": True, "path": str(path), "chars": len(text)})

    body_chars = body_length(text)
    logger.info(
        "蒸馏完成：run#%s -> %s 已更新（正文 %s 字符）", run_id, EXPERIENCE_SKILL, body_chars
    )
    return True, {
        "updated": True,
        "skill": EXPERIENCE_SKILL,
        "loaded_skills": loaded_names,
        "body_chars": body_chars,
        "total_chars": len(text),
    }


# ---------------------------------------------------------------- 后台任务（SD18）


async def _guarded(run_id: int) -> None:
    async with _lock:
        try:
            await run_distillation(run_id)
        except Exception:  # noqa: BLE001 - 后台任务绝不能把异常抛到事件循环外
            logger.exception("蒸馏任务异常（run#%s）", run_id)


def schedule(run_id: int) -> bool:
    """回合结束后调用：起一个后台任务。

    **不能挂在请求上** —— `_turn_stream` 的 finally 会 cancel 掉未完成的任务，
    而这里要跑 30~60 秒（等 Langfuse 最终一致 + 两轮模型调用）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.info("没有事件循环，跳过蒸馏（run#%s）", run_id)
        return False
    task = asyncio.create_task(_guarded(run_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
