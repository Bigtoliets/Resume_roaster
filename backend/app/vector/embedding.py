"""Embedding：稠密向量 + 稀疏向量两路来源。

provider:
  - remote : 调用任意 OpenAI 兼容的 /embeddings（默认，如 DashScope text-embedding-v4）
             自动分批（DashScope 单次上限 10 条）、超长截断、dimensions 参数自适应
  - local  : 本地 BGE-M3，FlagEmbedding 单次编码同时产出 dense + sparse
             需 pip install -e ".[embedding]"
  - fake   : 确定性哈希向量，仅供离线联调
  - auto   : 配了 EMBEDDING_API_KEY/BASE_URL 走 remote，否则尝试 local，最后 fake

稀疏向量说明（D33）：线上 `jd` collection 的 `sparse` 是 **BM25 Function 的输出字段**，
由 Milvus 从 `content` 自动生成 —— 写入和检索都不传稀疏向量，检索时那一行传的是查询文本。
所以本服务在检索链路里只提供 dense（`embed_dense`）。
`embed_hybrid` 仍保留给本地 BGE-M3（FlagEmbedding）场景用，与线上 schema 无关。
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from collections.abc import Sequence

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_+#\.]+|[\u4e00-\u9fff]")


class EmbeddingService:
    """线程安全的懒加载单例式服务。"""

    _lock = threading.Lock()

    def __init__(self) -> None:
        self.provider = settings.embedding_provider
        self.dim = settings.embedding_dim
        self._model = None  # type: ignore[var-annotated]
        # 接口是否接受 dimensions 参数：None=未知，首次 400 后置为 False
        self._dims_supported: bool | None = None

    # ---------- 对外接口 ----------

    async def embed_dense(self, texts: Sequence[str]) -> list[list[float]]:
        dense, _ = await self.embed_hybrid(texts)
        return dense

    async def embed_hybrid(
        self, texts: Sequence[str]
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        provider = self.resolve_provider()
        if provider == "local":
            return self._embed_local(texts)
        if provider == "remote":
            return await self._embed_remote(texts)
        return self._embed_fake(texts)

    # ---------- provider 解析 ----------

    def resolve_provider(self) -> str:
        if self.provider == "remote":
            if not settings.embedding_remote_ready:
                raise RuntimeError(
                    "EMBEDDING_PROVIDER=remote 但未配置 EMBEDDING_API_KEY / EMBEDDING_BASE_URL"
                )
            return "remote"
        if self.provider in {"local", "fake"}:
            return self.provider
        # auto：远程优先（配了 key 就用），否则本地模型，最后 fake
        if settings.embedding_remote_ready:
            return "remote"
        try:
            import FlagEmbedding  # noqa: F401

            return "local"
        except Exception:  # noqa: BLE001
            logger.warning(
                "未配置远程 embedding 且未安装 FlagEmbedding，退回 fake 实现"
                "（仅可用于开发联调，真实检索效果请配置 EMBEDDING_API_KEY）"
            )
            return "fake"

    # ---------- local：BGE-M3 ----------

    def _load_local(self):  # type: ignore[no-untyped-def]
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from FlagEmbedding import BGEM3FlagModel

                    logger.info("加载本地 embedding 模型：%s", settings.embedding_model)
                    self._model = BGEM3FlagModel(
                        settings.embedding_model, use_fp16=settings.embedding_device != "cpu"
                    )
        return self._model

    def _embed_local(
        self, texts: Sequence[str]
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        model = self._load_local()
        result = model.encode(
            list(texts), batch_size=8, max_length=8192, return_dense=True, return_sparse=True
        )
        dense = [list(map(float, vec)) for vec in result["dense_vecs"]]
        sparse: list[dict[int, float]] = []
        for item in result["lexical_weights"]:
            sparse.append({int(k): float(v) for k, v in item.items()})
        return dense, sparse

    # ---------- remote：OpenAI 兼容 embeddings ----------

    async def _embed_remote(
        self, texts: Sequence[str]
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        url = settings.embedding_url
        batch_size = max(1, int(settings.embedding_batch_size))
        dense: list[list[float]] = []

        async with httpx.AsyncClient(timeout=60) as client:
            for start in range(0, len(texts), batch_size):
                batch = [self._truncate(text) for text in texts[start : start + batch_size]]
                payload = await self._post_embeddings(client, url, batch)
                items = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
                dense.extend([list(map(float, item["embedding"])) for item in items])

        if dense and len(dense[0]) != self.dim:
            raise RuntimeError(
                f"embedding 维度不匹配：接口返回 {len(dense[0])} 维，"
                f"配置 EMBEDDING_DIM={self.dim}。请把 EMBEDDING_DIM 改成实际维度，"
                "并重建 Milvus collection（维度变更无法原地修改）"
            )
        return dense, [_sparse_from_text(text) for text in texts]

    async def _post_embeddings(
        self, client: httpx.AsyncClient, url: str, batch: list[str]
    ) -> dict:  # type: ignore[type-arg]
        headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
        payload: dict = {"model": settings.embedding_model, "input": batch}  # type: ignore[type-arg]
        if settings.embedding_send_dimensions and self._dims_supported is not False:
            payload["dimensions"] = self.dim

        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 400 and "dimensions" in payload:
            body = resp.text.lower()
            if "dimension" in body or "unsupported" in body or "invalid" in body:
                logger.warning(
                    "embedding 接口不接受 dimensions 参数，改用模型默认维度：%s", resp.text[:200]
                )
                self._dims_supported = False
                payload.pop("dimensions")
                resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()

    def _truncate(self, text: str) -> str:
        limit = int(settings.embedding_max_chars or 0)
        if limit and len(text) > limit:
            logger.warning(
                "文本长度 %d 超过 EMBEDDING_MAX_CHARS=%d，已截断；"
                "正式入库前应拆行为多行（见方案 §5.3 扩展规则）",
                len(text),
                limit,
            )
            return text[:limit]
        return text

    # ---------- fake：确定性，仅供开发 ----------

    def _embed_fake(
        self, texts: Sequence[str]
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        dense: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            seed = [b / 255.0 - 0.5 for b in digest]
            vec = [seed[i % len(seed)] for i in range(self.dim)]
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            dense.append([v / norm for v in vec])
        return dense, [_sparse_from_text(text) for text in texts]


def _sparse_from_text(text: str) -> dict[int, float]:
    """轻量稀疏表示：token 频率权重映射到整数维度（fake/remote 模式的兜底）。"""
    weights: dict[int, float] = {}
    for token in _TOKEN_RE.findall(text.lower()):
        bucket = int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % 100_000
        weights[bucket] = weights.get(bucket, 0.0) + 1.0
    return weights


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
