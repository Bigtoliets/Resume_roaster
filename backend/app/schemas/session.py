"""会话、消息与运行记录。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import Report


class SessionIn(BaseModel):
    agent_id: int | None = None
    title: str = "新会话"
    config_mode: str = "snapshot"  # snapshot | follow_latest


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int | None
    title: str
    status: str
    config_mode: str
    langfuse_session_id: str
    # 迁移前的历史行这些列是 NULL，声明成可选避免整表 500
    last_route: str | None = ""
    summary: str | None = ""
    summary_upto_message_id: int | None = 0
    created_at: datetime
    updated_at: datetime


class SessionUpdate(BaseModel):
    title: str | None = None
    config_mode: str | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    role: str
    type: str
    content: dict[str, Any]
    tokens: int
    latency_ms: int
    run_id: int | None
    langfuse_trace_id: str
    created_at: datetime


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    filename: str
    size: int
    sha256: str
    created_at: datetime


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    target_role: str
    status: str
    rounds: int
    elapsed_ms: int
    error: str
    langfuse_trace_id: str
    created_at: datetime


class DiagnosisRequest(BaseModel):
    target_role: str = Field(min_length=1, description="目标岗位，如「大数据开发实习生」")
    attachment_id: int | None = None
    resume_text: str | None = Field(
        default=None, description="直接粘贴的简历文本；与 attachment_id 二选一"
    )


class ResumeTextIn(BaseModel):
    """粘贴简历文本的入参。"""

    text: str = Field(min_length=50, description="简历正文（纯文本）")
    filename: str | None = None


class TurnRequest(BaseModel):
    """一次会话回合：统一入口，由路由决定走哪条链路。"""

    content: str = ""
    mode: str = "auto"  # auto | resume_diagnosis | chat
    target_role: str | None = None
    attachment_id: int | None = None
    resume_text: str | None = None


class DiagnosisAccepted(BaseModel):
    run_id: int
    session_id: int


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    report_json: dict[str, Any]


class SSEEvent(BaseModel):
    event: str
    data: dict[str, Any]


__all__ = [
    "AttachmentOut",
    "DiagnosisAccepted",
    "DiagnosisRequest",
    "MessageOut",
    "Report",
    "ReportOut",
    "RunOut",
    "SSEEvent",
    "SessionIn",
    "SessionOut",
]
