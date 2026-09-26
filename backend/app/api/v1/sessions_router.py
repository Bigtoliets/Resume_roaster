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
from app.agent.loop import run_react_turn
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
    Session,
    ToolCall,
)
from app.db.session import SessionLocal
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
from app.services.conversation import build_history, make_title, summarize_if_needed

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
            try:
                agent = await db.get(Agent, agent_id)
                session = await db.get(Session, session_id)
                if agent is None or session is None:
                    await emit("error", {"code": "NOT_FOUND", "msg": "会话或 Agent 不存在"})
                    return

                text = (payload.content or "").strip()
                if text:
                    db.add(
                        Message(
                            session_id=session_id,
                            role="user",
                            type="text",
                            content={"text": text},
                            run_id=run_id,
                        )
                    )
                    await db.commit()
                    invalidate_session(session_id)

                history = await build_history(db, session_id, 8)
                if history and history[-1]["role"] == "user":
                    history = history[:-1]
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
                            user_content=text,
                            history=history,
                            summary=session.summary or "",
                            attachments=attachments,
                            emit=emit,
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

                summary = await summarize_if_needed(db, session, None)
                if summary:
                    await emit("summary", {"summary": summary})
                await emit("done", {"run_id": run_id, "elapsed_ms": elapsed})
            except Exception as exc:  # noqa: BLE001
                logger.exception("ReAct 回合失败")
                await _mark_failed(db, run_id, f"{type(exc).__name__}: {exc}")
                await emit("error", {"code": "INTERNAL", "msg": str(exc), "retryable": True})

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
