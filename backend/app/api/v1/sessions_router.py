"""会话接口：ReAct 主循环的唯一入口（D31）。

- `POST /sessions/{id}/messages`  ：SSE，跑一轮完整的 ReAct（思考/行动/观察 + 最终回答）
- `GET  /tool-calls/{id}`          ：展开某次工具调用时拉完整参数与结果
- `POST /tool-calls/{id}/approve`  ：唤醒等待人工确认的循环
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.checkpoints import get as get_checkpoint
from app.agent.checkpoints import resolve_pending
from app.agent.checkpoints import snapshot as checkpoint_snapshot
from app.agent.loop import EmitFn, run_react_turn
from app.api.deps import get_db
from app.core.cache import (
    cache_stats,
    cached,
    invalidate_session,
    session_attachments_key,
    session_key,
    session_list_key,
    session_messages_key,
)
from app.core.config import settings
from app.db.base import utcnow
from app.db.models import (
    Agent,
    Attachment,
    Message,
    ModelConfig,
    Run,
    RunNote,
    Session,
    ToolCall,
)
from app.db.session import SessionLocal
from app.intent.config import INTENT_TIMEOUT_S, SLOT_TARGET_ROLE, TARGET_ROLE_ASK
from app.intent.pipeline import TurnIntent
from app.intent.pipeline import resolve as resolve_intent
from app.obs.langfuse import current_trace_id, observation, propagated, trace_url
from app.obs.langfuse import flush as flush_langfuse
from app.schemas.resource import ApprovalIn, ToolCallOut
from app.schemas.session import (
    AttachmentOut,
    MessageOut,
    RunOut,
    SessionIn,
    SessionOut,
    SessionUpdate,
)
from app.services.conversation import make_title

logger = logging.getLogger(__name__)
router = APIRouter(tags=["会话"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class SendMessageIn(BaseModel):
    content: str = ""
    agent_id: int | None = None
    attachment_id: int | None = None
    resume_text: str | None = None


class ResumeTextIn(BaseModel):
    text: str = Field(min_length=50)
    filename: str | None = None


# ---------------------------------------------------------------- 会话 CRUD


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    db: AsyncSession = Depends(get_db), limit: int = 100
) -> list[dict[str, Any]]:
    async def load() -> list[dict[str, Any]]:
        stmt = (
            select(Session)
            .where(Session.deleted_at.is_(None))
            .order_by(Session.id.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [SessionOut.model_validate(row).model_dump(mode="json") for row in rows]

    return await cached(session_list_key(), load)


@router.post("/sessions", response_model=SessionOut, status_code=201)
async def create_session(payload: SessionIn, db: AsyncSession = Depends(get_db)) -> Session:
    agent = await _pick_agent(db, payload.agent_id)
    session = Session(
        agent_id=agent.id if agent else None,
        title=payload.title or "新会话",
        config_mode=payload.config_mode,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    session.langfuse_session_id = f"session_{session.id}"
    await db.commit()
    await db.refresh(session)
    invalidate_session(session.id)
    return session


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    async def load() -> dict[str, Any]:
        row = await _get_session(db, session_id)
        return SessionOut.model_validate(row).model_dump(mode="json")

    return await cached(session_key(session_id), load)


@router.put("/sessions/{session_id}", response_model=SessionOut)
async def update_session(
    session_id: int, payload: SessionUpdate, db: AsyncSession = Depends(get_db)
) -> Session:
    session = await _get_session(db, session_id)
    if payload.title is not None and payload.title.strip():
        session.title = payload.title.strip()[:200]
    if payload.agent_id is not None:
        agent = await db.get(Agent, payload.agent_id)
        if agent is None or agent.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Agent 不存在")
        session.agent_id = agent.id
    await db.commit()
    await db.refresh(session)
    invalidate_session(session_id)
    return session


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: int, db: AsyncSession = Depends(get_db)) -> None:
    session = await _get_session(db, session_id)
    session.deleted_at = utcnow()
    await db.commit()
    invalidate_session(session_id)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: int, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    async def load() -> list[dict[str, Any]]:
        stmt = select(Message).where(Message.session_id == session_id).order_by(Message.id.asc())
        rows = (await db.execute(stmt)).scalars().all()
        return [MessageOut.model_validate(row).model_dump(mode="json") for row in rows]

    return await cached(session_messages_key(session_id), load)


@router.get("/sessions/{session_id}/attachments", response_model=list[AttachmentOut])
async def list_attachments(
    session_id: int, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    async def load() -> list[dict[str, Any]]:
        stmt = (
            select(Attachment)
            .where(Attachment.session_id == session_id)
            .order_by(Attachment.id.desc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [AttachmentOut.model_validate(row).model_dump(mode="json") for row in rows]

    return await cached(session_attachments_key(session_id), load)


@router.get("/sessions/{session_id}/notes")
async def list_run_notes(
    session_id: int, limit: int = 20, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    """会话当前有效的那一条任务笔记（D48 / D63）。

    返回 0 或 1 条：笔记现在是**就地覆盖**（一个会话一行），不再有版本历史 ——
    每次投影改同一行，旧版本只在 Langfuse 的 span 里。所以 `limit` 已经不起作用，
    保留参数只是为了不改动调用方签名。

    只返回 `pointer_scope == "session"` 的行：改造前的老笔记是回合级编号，在新作用域
    下指针必然歧义，既不会被注入模型、也不该冒充"模型此刻记得什么"（D62）。

    没跑过回合时返回空数组（正常现象：还没聊过）。
    """

    async def load() -> list[dict[str, Any]]:
        stmt = (
            select(RunNote)
            .where(RunNote.session_id == session_id, RunNote.pointer_scope == "session")
            .order_by(RunNote.id.desc())
            .limit(1)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        return [
            {
                "id": row.id,
                "run_id": row.run_id,
                "window_index": row.window_index,
                "source": row.source,
                "content": row.content or {},
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            for row in rows
        ]

    return await cached(f"session:{session_id}:notes", load)


@router.post("/sessions/{session_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    session_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
) -> Attachment:
    await _get_session(db, session_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".txt", ".md", ".markdown"}:
        raise HTTPException(status_code=400, detail="只支持 PDF 或纯文本（.txt / .md）")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（上限 10MB）")
    return await _store_attachment(db, session_id, content, suffix, file.filename or "简历")


@router.post("/sessions/{session_id}/resume-text", response_model=AttachmentOut, status_code=201)
async def save_resume_text(
    session_id: int, payload: ResumeTextIn, db: AsyncSession = Depends(get_db)
) -> Attachment:
    await _get_session(db, session_id)
    text = payload.text.strip()
    if len(text) < 50:
        raise HTTPException(status_code=400, detail="文本过短（不足 50 字）")
    return await _store_attachment(
        db, session_id, text.encode("utf-8"), ".txt", payload.filename or "粘贴的简历.txt"
    )


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


# ---------------------------------------------------------------- 工具调用


@router.get("/tool-calls/{tool_call_id}", response_model=ToolCallOut)
async def get_tool_call(tool_call_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await db.get(ToolCall, tool_call_id)
    if row is None:
        raise HTTPException(status_code=404, detail="工具调用不存在")
    return ToolCallOut.model_validate(row).model_dump(mode="json")


@router.post("/tool-calls/{tool_call_id}/approve")
async def approve_tool_call(tool_call_id: int, payload: ApprovalIn) -> dict[str, Any]:
    """唤醒等待确认的 ReAct 回合。

    两种决定：
      - `approved=true`  → 执行（`remember=true` 则本回合同工具不再问）
      - `approved=false` → 取消（`remember=true` 则本回合同工具直接拒，不再弹卡片）

    没有等待中的调用时 `waiting=false`（说明已超时或已处理过）。
    """
    decision = "approved" if payload.approved else "cancelled"
    waiting = resolve_pending(tool_call_id, decision, remember=payload.remember)
    return {
        "tool_call_id": tool_call_id,
        "decision": decision,
        "remember": payload.remember,
        "waiting": waiting,
    }


@router.get("/runs/{run_id}/checkpoint")
async def get_run_checkpoint(run_id: int) -> dict[str, Any]:
    """取一个正在跑的回合的进程内状态：刷新页面后前端据此重建时间线与待确认卡片。

    回合不在内存里（已结束、或后端重启过）返回 404，前端退回"从 messages 表读历史"。
    """
    checkpoint = get_checkpoint(run_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="该回合不在内存中（可能已结束或后端重启过）")
    return checkpoint.dump()


@router.get("/checkpoints/stats")
async def checkpoint_stats() -> dict[str, Any]:
    """诊断用：当前有几个回合在跑、几个在等确认。"""
    return checkpoint_snapshot()


@router.get("/cache/stats")
async def cache_status() -> dict[str, float]:
    return cache_stats()


# ---------------------------------------------------------------- ReAct 回合（SSE）


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int, payload: SendMessageIn, db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    session = await _get_session(db, session_id)
    current_agent_id = payload.agent_id or session.agent_id
    agent = await db.get(Agent, current_agent_id) if current_agent_id else None
    if agent is None or agent.deleted_at is not None:
        agent = await _pick_agent(db, None)
    if agent is None:
        raise HTTPException(status_code=400, detail="还没有可用的 Agent，请先在 Agents 页面创建")
    if session.agent_id != agent.id:
        session.agent_id = agent.id
        await db.commit()
        invalidate_session(session_id)

    attachment: Attachment | None = None
    if (payload.resume_text or "").strip():
        attachment = await _store_attachment(
            db,
            session_id,
            (payload.resume_text or "").encode("utf-8"),
            ".txt",
            "粘贴的简历.txt",
        )
    elif payload.attachment_id:
        attachment = await db.get(Attachment, payload.attachment_id)

    run = Run(session_id=session_id, target_role="", status="running")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    return StreamingResponse(
        _turn_stream(
            session_id=session_id,
            run_id=run.id,
            agent_id=agent.id,
            payload=payload,
            attachment=attachment,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _turn_stream(
    *,
    session_id: int,
    run_id: int,
    agent_id: int,
    payload: SendMessageIn,
    attachment: Attachment | None,
) -> AsyncIterator[str]:
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    started = time.perf_counter()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def worker() -> None:
        async with SessionLocal() as db:
            # 追问轮不跑主循环，也就没有可蒸馏的东西（Q10）
            distill = True
            try:
                agent = await db.get(Agent, agent_id)
                session = await db.get(Session, session_id)
                if agent is None or session is None:
                    await emit("error", {"code": "NOT_FOUND", "msg": "会话或 Agent 不存在"})
                    return

                text = (payload.content or "").strip()
                #: 本轮用户消息的主键。传给主循环去盖存档坐标（`W1.M1`），
                #: 没它的话笔记里引用的消息级指针永远查不到行。
                user_message_id: int | None = None
                if text:
                    user_message = Message(
                        session_id=session_id,
                        role="user",
                        type="text",
                        content={"text": text},
                        run_id=run_id,
                    )
                    db.add(user_message)
                    await db.commit()
                    await db.refresh(user_message)
                    user_message_id = user_message.id
                    invalidate_session(session_id)

                # D53：不再拼"最近 8 条聊天记录"的全文 —— 跨回合的连续性改由
                # 「最新一条任务笔记 + 按指针回查原文」承担（见 loop.load_notes）。
                attachments = (
                    [
                        {
                            "filename": attachment.filename,
                            "path": attachment.stored_path,
                            "size": attachment.size,
                        }
                    ]
                    if attachment is not None
                    else []
                )

                # ── 意图层：主循环之前的页面参数归一化（Q1）─────────────────
                # 只把用户输入归一化成槽位、并决定要不要追问，不改变"谁来回话"。
                # 任何失败都降级成空槽位，照常进主循环（Q5）——所以这里绝不抛异常。
                inherited_role = await _inherited_role(db, session_id)
                pending_slot = await _pending_slot(db, session_id)
                intent = await _resolve_intent(
                    session_id=session_id,
                    text=text,
                    inherited_role=inherited_role,
                    pending_slot=pending_slot,
                )
                # 继承优先于追问（Q8）；本轮抽到的值覆盖继承值
                target_role = (intent.target_role or inherited_role)[:128]
                if target_role:
                    await _remember_target_role(db, run_id, target_role)

                if intent.ask:
                    await _ask_for_slot(
                        db,
                        session_id=session_id,
                        run_id=run_id,
                        session=session,
                        agent=agent,
                        user_text=text,
                        slot=intent.slot or SLOT_TARGET_ROLE,
                        emit=emit,
                        started=started,
                    )
                    distill = False
                    return

                await emit(
                    "stage",
                    {
                        "stage": "thinking",
                        "status": "running",
                        "agent": agent.name,
                        "max_rounds": agent.max_rounds,
                    },
                )

                # Langfuse 上显示的模型名来自 API 响应，可能被上游改写
                # （DeepSeek 对 deepseek-chat / deepseek-reasoner 都回报 deepseek-flash）。
                # 把"我们请求的模型"写进 metadata，排查时能直接对照，不用再猜。
                model_row = (
                    await db.get(ModelConfig, agent.model_config_id)
                    if agent.model_config_id
                    else None
                )
                configured_model = model_row.model_name if model_row else settings.default_chat_model

                # 一个回合 = 一个 trace；多轮靠 session_id 聚成一次会话（官方 v4 规范）
                with propagated(session_id=f"session_{session_id}"):
                    with observation(
                        "react-turn",
                        as_type="agent",
                        # root 的 input/output 决定 tracing 列表里显示什么，放人能一眼看懂的内容；
                        # 完整的 messages / 工具 payload 在各自的 observation 里
                        input={"query": text, "agent": agent.name},
                        metadata={
                            "session_id": session_id,
                            "run_id": run_id,
                            "requested_model": configured_model,
                        },
                    ) as agent_span:
                        trace_id = current_trace_id()
                        result = await run_react_turn(
                            db,
                            session_id=session_id,
                            run_id=run_id,
                            agent=agent,
                            user_content=_compose_turn_text(text, target_role),
                            attachments=attachments,
                            emit=emit,
                            user_message_id=user_message_id,
                        )
                        if agent_span is not None:
                            with contextlib.suppress(Exception):
                                agent_span.update(output={"answer": result.text})

                elapsed = int((time.perf_counter() - started) * 1000)
                assistant = Message(
                    session_id=session_id,
                    role="assistant",
                    type="text",
                    content={
                        "text": result.text,
                        "steps": result.steps,
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                        "rounds": result.rounds,
                        "tool_call_ids": result.tool_call_ids,
                    },
                    run_id=run_id,
                    latency_ms=elapsed,
                    langfuse_trace_id=trace_id,
                    # D48 存档坐标：让这条回答也能被 `W*.M*` 指针回查
                    window_index=result.window_index,
                    seq=result.seq,
                )
                db.add(assistant)
                run_row = await db.get(Run, run_id)
                if run_row:
                    run_row.status = "succeeded"
                    run_row.elapsed_ms = elapsed
                    run_row.langfuse_trace_id = trace_id
                if not session.title or session.title.startswith("新会话"):
                    session.title = make_title(text or "新会话", agent.name)
                await db.commit()
                await db.refresh(assistant)
                invalidate_session(session_id)
                flush_langfuse()

                await emit(
                    "assistant",
                    {
                        "message_id": assistant.id,
                        "content": result.text,
                        "steps": result.steps,
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                        "rounds": result.rounds,
                        "elapsed_ms": elapsed,
                        "langfuse_url": trace_url(trace_id),
                    },
                )

                await emit("done", {"run_id": run_id, "elapsed_ms": elapsed})
            except Exception as exc:  # noqa: BLE001
                logger.exception("ReAct 回合失败")
                await _mark_failed(db, run_id, f"{type(exc).__name__}: {exc}")
                await emit("error", {"code": "INTERNAL", "msg": str(exc), "retryable": True})
            finally:
                if distill:
                    # Skill 蒸馏线路（SD6 / SD18）：**成功失败都触发**，因为"坑"不只在失败里。
                    # 必须另起独立任务：本函数的 finally 会 cancel 掉未完成的 task，
                    # 而蒸馏要跑 30~60 秒（等 Langfuse 最终一致 + 两轮模型调用）。
                    with contextlib.suppress(Exception):
                        from app.skill.distill import schedule

                        schedule(run_id)

    task = asyncio.create_task(worker())
    try:
        while True:
            if task.done() and queue.empty():
                break
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield _sse(event, data)
    finally:
        if not task.done():
            task.cancel()


# ---------------------------------------------------------------- 内部工具


async def _get_session(db: AsyncSession, session_id: int) -> Session:
    session = await db.get(Session, session_id)
    if session is None or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


async def _pick_agent(db: AsyncSession, agent_id: int | None) -> Agent | None:
    if agent_id:
        row = await db.get(Agent, agent_id)
        return row if row and row.deleted_at is None else None
    # 优先选"挂了工具"的 Agent：没工具的主循环只会干说话
    stmt = (
        select(Agent)
        .where(Agent.deleted_at.is_(None), Agent.is_enabled.is_(True))
        .order_by(Agent.id.desc())
        .limit(1)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    for row in rows:
        if row.tool_ids:
            return row
    return rows[0] if rows else None


async def _store_attachment(
    db: AsyncSession, session_id: int, content: bytes, suffix: str, filename: str
) -> Attachment:
    digest = hashlib.sha256(content).hexdigest()
    target = settings.upload_path / f"{session_id}_{digest[:12]}{suffix}"
    target.write_bytes(content)
    attachment = Attachment(
        session_id=session_id,
        filename=filename,
        stored_path=str(target),
        size=len(content),
        sha256=digest,
    )
    db.add(attachment)
    await db.flush()
    db.add(
        Message(
            session_id=session_id,
            role="user",
            type="file",
            content={"filename": filename, "attachment_id": attachment.id, "size": len(content)},
        )
    )
    await db.commit()
    await db.refresh(attachment)
    invalidate_session(session_id)
    return attachment


async def _mark_failed(db: AsyncSession, run_id: int, message: str) -> None:
    run_row = await db.get(Run, run_id)
    if run_row:
        run_row.status = "failed"
        run_row.error = message[:2000]
        await db.commit()


# ---------------------------------------------------------------- 意图层


def _compose_turn_text(text: str, target_role: str) -> str:
    """把归一化出来的槽位拼在用户消息头部（Q2）。

    只在真的取到岗位时才注入——字段为空时不写空壳，免得给模型制造噪声。
    """
    body = (text or "").strip()
    if not target_role:
        return body
    head = f"【本轮输入】\n目标岗位：{target_role}"
    return f"{head}\n\n{body}" if body else head


async def _pending_slot(db: AsyncSession, session_id: int) -> str:
    """上一条助手消息如果是追问，返回它要的槽位名（Q16）。

    只看最后一条 assistant 消息：追问之后用户已经回了话，只要那条追问仍然是
    最近的一条助手消息，就说明用户正在回答它。
    """
    stmt = (
        select(Message)
        .where(Message.session_id == session_id, Message.role == "assistant")
        .order_by(Message.id.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalars().first()
    if row is None or not isinstance(row.content, dict):
        return ""
    if row.content.get("kind") != "ask":
        return ""
    return str(row.content.get("slot") or "")


async def _inherited_role(db: AsyncSession, session_id: int) -> str:
    """本会话最近一次非空的目标岗位（Q8：继承优先于追问）。"""
    stmt = (
        select(Run.target_role)
        .where(Run.session_id == session_id, Run.target_role != "")
        .order_by(Run.id.desc())
        .limit(1)
    )
    return str((await db.execute(stmt)).scalars().first() or "")


async def _remember_target_role(db: AsyncSession, run_id: int, target_role: str) -> None:
    """把岗位写到本轮的 Run 上（Q17）。继承来的值也回填，报告 meta 才不会时有时无。"""
    run_row = await db.get(Run, run_id)
    if run_row is None or run_row.target_role == target_role:
        return
    run_row.target_role = target_role
    await db.commit()


async def _resolve_intent(
    *, session_id: int, text: str, inherited_role: str, pending_slot: str
) -> TurnIntent:
    """意图层唯一的调用点：硬超时 + 绝不抛异常（Q5）。

    判定结果不落库（Q13），所以顺手写进 Langfuse 的 span——出问题时至少有得看。
    用 `propagated` 挂上 session，这条 span 才和本轮 ReAct 归到同一个会话视图里。
    """
    try:
        with propagated(session_id=f"session_{session_id}"):
            with observation(
                "intent",
                input={"text": text, "pending_slot": pending_slot, "inherited_role": inherited_role},
            ) as span:
                result = await asyncio.wait_for(
                    resolve_intent(
                        text=text, inherited_role=inherited_role, pending_slot=pending_slot
                    ),
                    timeout=INTENT_TIMEOUT_S,
                )
                if span is not None:
                    with contextlib.suppress(Exception):
                        span.update(
                            output={
                                "intent": result.intent,
                                "target_role": result.target_role,
                                "ask": result.ask,
                            },
                            metadata={"source": result.source, "confidence": result.confidence},
                        )
                return result
    except Exception as exc:  # noqa: BLE001 - 降级放行，绝不阻塞主循环
        logger.warning("意图层降级（%s）：%s", type(exc).__name__, exc)
        return TurnIntent()


async def _ask_for_slot(
    db: AsyncSession,
    *,
    session_id: int,
    run_id: int,
    session: Session,
    agent: Agent,
    user_text: str,
    slot: str,
    emit: EmitFn,
    started: float,
) -> None:
    """追问一轮：照常落一条 assistant 消息，只是没跑主循环（Q10）。

    没有新增 SSE 事件类型：复用 `assistant` + `done`，多带一个 `kind` 标记，
    前端据此换成追问气泡。
    """
    elapsed = int((time.perf_counter() - started) * 1000)
    assistant = Message(
        session_id=session_id,
        role="assistant",
        type="text",
        content={
            "text": TARGET_ROLE_ASK,
            "steps": [],
            "agent_id": agent.id,
            "agent_name": agent.name,
            "kind": "ask",
            "slot": slot,
        },
        run_id=run_id,
        latency_ms=elapsed,
    )
    db.add(assistant)
    run_row = await db.get(Run, run_id)
    if run_row is not None:
        run_row.status = "succeeded"
        run_row.elapsed_ms = elapsed
    if not session.title or session.title.startswith("新会话"):
        session.title = make_title(user_text or "新会话", agent.name)
    await db.commit()
    await db.refresh(assistant)
    invalidate_session(session_id)
    await emit(
        "assistant",
        {
            "message_id": assistant.id,
            "content": TARGET_ROLE_ASK,
            "steps": [],
            "kind": "ask",
            "slot": slot,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "rounds": 0,
            "elapsed_ms": elapsed,
        },
    )
    await emit("done", {"run_id": run_id, "elapsed_ms": elapsed})
