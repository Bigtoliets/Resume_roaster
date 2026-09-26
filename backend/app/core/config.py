"""全局配置：统一从项目根的 .env 读取（与 scripts/check_env.py 共用同一份配置）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：backend/app/core/config.py -> 上溯 3 层
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 应用 ----------
    app_env: str = "local"
    api_port: int = 8000
    frontend_port: int = 5173
    log_level: str = "INFO"
    upload_dir: str = "./uploads"
    max_rounds: int = 5
    diagnosis_timeout_s: int = 300

    # ---------- MySQL ----------
    mysql_host: str = ""
    mysql_port: int = 3306
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_db: str = "resume_roaster"
    mysql_charset: str = "utf8mb4"

    # ---------- Milvus ----------
    milvus_host: str = ""
    milvus_port: int = 19530
    milvus_user: str = ""
    milvus_password: str = ""
    milvus_db: str = "default"

    # ---------- Langfuse ----------
    langfuse_base_url: str = ""
    langfuse_host: str = ""
    langfuse_public_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_project_id: str = ""
    # trace 跳转模板：{host} / {project_id} / {trace_id} 会被替换
    langfuse_trace_url_template: str = "{host}/project/{project_id}/traces/{trace_id}"

    # ---------- LLM ----------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    default_chat_model: str = "deepseek-chat"
    default_reasoner_model: str = "deepseek-reasoner"

    # ---------- Embedding ----------
    # provider: auto | remote | local | fake
    #   auto   = 配了 API Key 走 remote，否则尝试本地 BGE-M3，最后退回 fake
    #   remote = 任意 OpenAI 兼容的 /embeddings（如 DashScope text-embedding-v4）
    #   local  = 本地 BGE-M3（FlagEmbedding，需 pip install -e ".[embedding]"）
    #   fake   = 确定性哈希向量，仅供离线联调
    embedding_provider: str = "auto"
    embedding_model: str = Field(
        "text-embedding-v4",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "OPENAI_EMBED_MODEL"),
    )
    embedding_dim: int = 1024
    embedding_device: str = "cpu"
    embedding_api_key: str = Field(
        "",
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "OPENAI_EMBED_API_KEY"),
    )
    embedding_base_url: str = Field(
        "",
        validation_alias=AliasChoices("EMBEDDING_BASE_URL", "OPENAI_EMBED_BASE_URL"),
    )
    # DashScope 单次最多 10 条，超过会报 400，因此必须分批
    embedding_batch_size: int = 10
    # 单条文本超长保护（超过则截断并告警；正式做法是入库前拆行）
    embedding_max_chars: int = 8000
    embedding_send_dimensions: bool = True
    # 稀疏向量来源：bm25_function（Milvus 2.5 内置 BM25，推荐）| client_side（词袋近似）
    milvus_sparse_mode: str = "bm25_function"

    # ---------- 安全 ----------
    secret_key: str = ""

    @property
    def langfuse_url(self) -> str:
        return (self.langfuse_base_url or self.langfuse_host or "").rstrip("/")

    @property
    def langfuse_public_url(self) -> str:
        return (self.langfuse_public_host or self.langfuse_url).rstrip("/")

    @property
    def mysql_dsn(self) -> str:
        return (
            f"mysql+asyncmy://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset={self.mysql_charset}"
        )

    @property
    def milvus_uri(self) -> str:
        return f"http://{self.milvus_host}:{self.milvus_port}"

    @property
    def upload_path(self) -> Path:
        path = Path(self.upload_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_url and self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def langfuse_trace_template(self) -> str:
        """未配置 project_id 时退回 /trace/{id} 形式。"""
        if self.langfuse_project_id:
            return self.langfuse_trace_url_template
        return "{host}/trace/{trace_id}"

    @property
    def mysql_configured(self) -> bool:
        return bool(self.mysql_host and self.mysql_db)

    @property
    def milvus_configured(self) -> bool:
        return bool(self.milvus_host)

    @property
    def embedding_remote_ready(self) -> bool:
        return bool(self.embedding_api_key and self.embedding_base_url)

    @property
    def embedding_url(self) -> str:
        """归一化成 OpenAI 兼容的 /embeddings 地址。"""
        base = (self.embedding_base_url or "").rstrip("/")
        if not base:
            return ""
        if base.endswith("/v1"):
            return f"{base}/embeddings"
        return f"{base}/v1/embeddings"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
