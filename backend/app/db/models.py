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
    #: 模型的最大上下文窗口（token）。窗口换页的阈值按它算比例（D48）。
    context_window: Mapped[int] = mapped_column(Integer, default=65536)
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
    #: 存档坐标，语义同 Message：指针 W2.T3 靠这两列定位。
    window_index: Mapped[int] = mapped_column(Integer, default=0)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RunNote(Base):
    """任务笔记（D48 / D63）：跨窗口、跨回合唯一的连续性来源。

    写它的时机有两个（D60）：**换页时**、**回合收尾时**。

    **D63 起是就地覆盖**：一个会话只保留一行，每次投影改同一行（`upsert_notes()`）。
    改造前是"每换一次页追加一条、老的不动"的多版本表，前端靠它做版本回看；
    覆盖之后版本历史改由 Langfuse 承担（`memory-rollover` / `turn-projection` 两个 span）。
    老的多版本行不会被删，但 `list_run_notes` 只返回最新一条、注入时也只取最新一条。
    """

    __tablename__ = "run_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, index=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    #: 写这份笔记时的窗口号
    window_index: Mapped[int] = mapped_column(Integer, default=1)
    #: 笔记正文：goal / progress / decisions / plan / refs
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: 生成方式：projection（正常换页投影）/ recovery（回校修正）
    source: Mapped[str] = mapped_column(String(16), default="projection")
    #: 指针作用域（D50/D62）：`session` = 会话级编号（改造后），`run` = 回合级编号（改造前）。
    #: 注入时只认 `session` —— 老笔记里的指针在新作用域下必然歧义（同一会话有多行 W1），
    #: 注进去只会让模型拿着一串注定报错的指针空转。
    pointer_scope: Mapped[str] = mapped_column(String(8), default="run")
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
    """平台资源索引（SD16）：**正文在文件里**，这张表只存名字与描述。

    `backend/skill/<name>/SKILL.md` 是唯一真源；这张表是索引，靠"启动对账 +
    写路径同步"保持一致（见 `app/skill/sync.py`）。

    `instructions` 列已删除 —— 正文搬到文件后，留在库里就只会制造
    "表说 A、盘上说 B" 的第二真相。
    """

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(1024), default="")
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
    #: 上下文用量超过 context_window 的这个百分比就换页（D48）。80 = 到 80% 触发。
    context_threshold_pct: Mapped[int] = mapped_column(Integer, default=80)
    #: 同一轮里模型一次调多个工具时，是否并发执行（默认开）
    parallel_tools: Mapped[bool] = mapped_column(Boolean, default=True)
    #: 回合结束后是否自动跑 skill 蒸馏线路（SD22，默认关）
    auto_distill: Mapped[bool] = mapped_column(Boolean, default=False)
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
    #: 存档坐标（D48）：属于第几个上下文窗口、窗口内第几条。
    #: 消息与工具调用共用一个 seq 计数器，所以指针 W2.M5 / W2.T3 唯一。
    #: 0 表示不属于任何窗口（历史数据）。
    window_index: Mapped[int] = mapped_column(Integer, default=0)
    seq: Mapped[int] = mapped_column(Integer, default=0)


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
