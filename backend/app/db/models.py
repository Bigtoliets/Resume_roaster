"""全部 MySQL 表定义（对应方案 §6）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.base import Base, TimestampMixin, utcnow


class ModelConfig(Base, TimestampMixin):
    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    base_url: Mapped[str] = mapped_column(String(255), default="")
    api_key_cipher: Mapped[str] = mapped_column(Text, default="")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    default_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timeout_s: Mapped[int] = mapped_column(Integer, default=120)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Prompt(Base, TimestampMixin):
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    variables: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Tool(Base, TimestampMixin):
    """工具：ReAct 循环里唯一的能力单元（D32）。

    页面这行 = 注册表里的一条实例配置；真正能做什么由代码决定：
      - `impl_key` 指向 `app/tools/<impl_key>.py` 里的实现；命中不到就是「未实现」
      - 类别（要不要模型 / 要不要 Prompt）也由实现声明，页面不再选
      - `params_schema` / `returns_schema` / `impl_config` 是页面上三个 JSON 框，
        页面填什么就存什么（代码里的同名声明只是新建时的默认值）
    """

    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(String(255), default="")
    impl_key: Mapped[str] = mapped_column(String(64), default="")
    prompt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    model_config_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_s: Mapped[int] = mapped_column(Integer, default=120)
    #: 执行失败后的重试次数（只对超时/限流/服务端错误重试）
    retry_times: Mapped[int] = mapped_column(Integer, default=1)
    params_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    returns_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    impl_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    test_cases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ToolCall(Base):
    """一次工具调用：完整参数与完整结果存这里，消息里只留摘要（展开时再按需拉取）。"""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tool_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), default="")
    round_index: Mapped[int] = mapped_column(Integer, default=0)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error|rejected|pending_approval
    error: Mapped[str] = mapped_column(Text, default="")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    vector_store: Mapped[str] = mapped_column(String(32), default="milvus")
    collection: Mapped[str] = mapped_column(String(64), default="jd")
    # 跟随 .env 的 EMBEDDING_MODEL：入库时记录"这批向量是谁产的"，
    # 换模型必须重建 collection，所以这个值不能与配置漂移。
    embedding_model: Mapped[str] = mapped_column(
        String(128), default=lambda: settings.embedding_model
    )
    chunk_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    top_k: Mapped[int] = mapped_column(Integer, default=10)
    # ---- 检索行为（页面可调，D35）----
    rrf_k: Mapped[int] = mapped_column(Integer, default=60)
    recall_multiplier: Mapped[int] = mapped_column(Integer, default=3)
    snippet_chars: Mapped[int] = mapped_column(Integer, default=1200)
    min_score: Mapped[float] = mapped_column(Float, default=0.0)
    filter_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Skill(Base, TimestampMixin):
    """独立的文本增强单元：只含指令，不引用任何其他资源（D25）。"""

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Agent(Base, TimestampMixin):
    """人设单元：Model + Prompt + Persona + Skills（不含工具与语料，D25）。"""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    persona: Mapped[str] = mapped_column(Text, default="")
    agent_type: Mapped[str] = mapped_column(String(16), default="general")  # general|diagnosis
    model_config_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prompt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    skill_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    tool_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    max_rounds: Mapped[int] = mapped_column(Integer, default=12)
    #: 同一轮里模型一次调多个工具时，是否并发执行（默认开）
    parallel_tools: Mapped[bool] = mapped_column(Boolean, default=True)
    params_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ResourceVersion(Base):
    """不可变快照：Agent 保存时对其引用的全部资源打版本。"""

    __tablename__ = "resource_versions"
    __table_args__ = (UniqueConstraint("resource_type", "resource_id", "version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    agent_version_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_mode: Mapped[str] = mapped_column(String(16), default="snapshot")
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    status: Mapped[str] = mapped_column(String(16), default="active")
    langfuse_session_id: Mapped[str] = mapped_column(String(64), default="")
    #: 会话级滚动摘要与水位线（摘要已覆盖到哪条消息）
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_upto_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    #: 最近一次路由结果（记录用，不注入模型）
    last_route: Mapped[str] = mapped_column(String(64), default="")


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(16), default="assistant")
    type: Mapped[str] = mapped_column(String(16), default="text")  # text|file|progress|report|debate_trace
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    langfuse_trace_id: Mapped[str] = mapped_column(String(64), default="")


class Attachment(Base, TimestampMixin):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")


class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_role: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="running")
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    langfuse_trace_id: Mapped[str] = mapped_column(String(64), default="")


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, index=True)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pdf_path: Mapped[str] = mapped_column(String(512), default="")
