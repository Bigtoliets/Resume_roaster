"""六类平台资源的出入参。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ModelConfigIn(BaseModel):
    name: str
    description: str = ""
    base_url: str = ""
    api_key: str = ""  # 明文入参，入库前加密
    model_name: str = ""
    default_params: dict[str, Any] = Field(default_factory=dict)
    timeout_s: int = 120
    is_default: bool = False
    is_enabled: bool = True


class ModelConfigOut(ORMBase):
    id: int
    name: str
    description: str
    base_url: str
    api_key_masked: str = ""
    model_name: str
    default_params: dict[str, Any]
    timeout_s: int
    is_default: bool
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class PromptIn(BaseModel):
    name: str
    description: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    variables: list[str] = Field(default_factory=list)
    is_enabled: bool = True


class PromptOut(ORMBase):
    id: int
    name: str
    description: str
    messages: list[dict[str, Any]]
    params: dict[str, Any]
    variables: list[str]
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class ToolIn(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    impl_key: str = ""
    prompt_id: int | None = None
    model_config_id: int | None = None
    requires_approval: bool = False
    timeout_s: int = 120
    retry_times: int = 1
    #: 页面上三个 JSON 框：给模型看的入参、出参（仅存档/展示）、运行时配置
    params_schema: dict[str, Any] = Field(default_factory=dict)
    returns_schema: dict[str, Any] = Field(default_factory=dict)
    impl_config: dict[str, Any] = Field(default_factory=dict)
    test_cases: list[dict[str, Any]] = Field(default_factory=list)
    is_enabled: bool = True


class ToolOut(ORMBase):
    id: int
    name: str
    display_name: str
    description: str
    impl_key: str
    prompt_id: int | None
    model_config_id: int | None
    requires_approval: bool
    timeout_s: int
    retry_times: int
    params_schema: dict[str, Any]
    returns_schema: dict[str, Any]
    impl_config: dict[str, Any]
    test_cases: list[dict[str, Any]]
    is_enabled: bool
    created_at: datetime
    updated_at: datetime
    # ---- 由代码注册表推导，不入库 ----
    impl_status: str = "unset"  # ok | missing | unset
    impl_title: str = ""
    impl_kind: str = ""  # llm | builtin | ""
    needs_prompt: bool = False


class KnowledgeBaseIn(BaseModel):
    name: str
    description: str = ""
    vector_store: str = "milvus"
    collection: str = "jd"
    # 默认跟随 .env 的 EMBEDDING_MODEL，避免换模型后这里残留旧值造成误导
    embedding_model: str = Field(default_factory=lambda: settings.embedding_model)
    chunk_config: dict[str, Any] = Field(default_factory=dict)
    top_k: int = 10
    rrf_k: int = 60
    recall_multiplier: int = 3
    snippet_chars: int = 1200
    min_score: float = 0.0
    filter_fields: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True


class KnowledgeBaseOut(ORMBase):
    id: int
    name: str
    description: str
    vector_store: str
    collection: str
    embedding_model: str
    chunk_config: dict[str, Any]
    top_k: int
    rrf_k: int
    recall_multiplier: int
    snippet_chars: int
    min_score: float
    filter_fields: dict[str, Any]
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class SkillIn(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    is_enabled: bool = True


class SkillOut(ORMBase):
    id: int
    name: str
    description: str
    instructions: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentIn(BaseModel):
    name: str
    description: str = ""
    persona: str = ""
    agent_type: str = "general"
    model_config_id: int | None = None
    prompt_id: int | None = None
    skill_ids: list[int] = Field(default_factory=list)
    tool_ids: list[int] = Field(default_factory=list)
    max_rounds: int = 12
    parallel_tools: bool = True
    params_override: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True


class AgentOut(ORMBase):
    id: int
    name: str
    description: str
    persona: str
    agent_type: str
    model_config_id: int | None
    prompt_id: int | None
    skill_ids: list[int]
    tool_ids: list[int]
    max_rounds: int
    parallel_tools: bool
    params_override: dict[str, Any]
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class ResourceRef(BaseModel):
    """删除保护：返回引用该资源的 Agent 列表。"""

    type: str
    id: int
    referenced_by: list[dict[str, Any]] = Field(default_factory=list)


class ToolImplOut(BaseModel):
    """后端注册的工具实现（工具页选它 + 填配置）。"""

    impl_key: str
    title: str
    description: str
    kind: str = "builtin"
    needs_prompt: bool = False
    params_schema: dict[str, Any] = Field(default_factory=dict)
    returns_schema: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    default_timeout_s: int = 120


class ToolCallOut(ORMBase):
    """工具调用详情：列表只给摘要，展开时才拉这个。"""

    id: int
    session_id: int
    message_id: int | None
    tool_id: int | None
    tool_name: str
    round_index: int
    arguments: dict[str, Any]
    result: dict[str, Any]
    summary: str
    status: str
    error: str
    requires_approval: bool
    approved: bool | None
    elapsed_ms: int
    created_at: datetime


class ApprovalIn(BaseModel):
    approved: bool
