"""Milvus 客户端：jd collection 的 schema 与混合检索。

Schema（与线上 collection 对齐，4 个字段）：

    id       VarChar(128)   主键，如 bigdata_jd_001
    content  VarChar(65535) 正文，enable_analyzer=True
    dense    FloatVector(1024)
    sparse   SparseFloatVector —— **由 BM25 Function 从 content 自动生成**

关键点：`sparse` 是 function 的输出字段，所以
  1. **写入时不能传 sparse**（只写 id / content / dense），BM25 由 Milvus 自己算；
  2. **检索时这一路传的是查询文本**，不是向量：`data=[query_text]` + `metric_type="BM25"`。
dense 路仍然由 embedding 服务提供向量，两路 RRF 融合。
"""

from __future__ import annotations

import contextlib
import logging
import re
from functools import lru_cache
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

ID_FIELD = "id"
TEXT_FIELD = "content"
DENSE_FIELD = "dense"
SPARSE_FIELD = "sparse"
BM25_FUNCTION = "bm25_func"

#: 一期只维护 JD 一个 collection；面经表已下线
COLLECTIONS = ("jd",)

#: 兜底值：线上 jd 的 dense 索引是 IP，sparse 是 BM25。实际以 collection 上的索引为准。
DEFAULT_DENSE_METRIC = "IP"
DEFAULT_SPARSE_METRIC = "BM25"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)")


class MilvusNotConfigured(RuntimeError):
    pass


@lru_cache
def get_client():  # type: ignore[no-untyped-def]
    if not settings.milvus_configured:
        raise MilvusNotConfigured("未配置 MILVUS_HOST，请在 .env 中填写")
    from pymilvus import MilvusClient

    token = ""
    if settings.milvus_user:
        token = f"{settings.milvus_user}:{settings.milvus_password}"
    return MilvusClient(uri=settings.milvus_uri, token=token or None, db_name=settings.milvus_db)


def server_version() -> str:
    """读取服务端版本。失败时抛错，由调用方决定如何呈现。"""
    return str(get_client().get_server_version())


def parse_version(raw: str) -> tuple[int, int] | None:
    """从 'pkg/v2.5.4' 这类字符串里取出 (2, 5)；解析不出返回 None。"""
    match = _VERSION_RE.search(raw or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


@lru_cache
def index_metric(collection: str, field: str, default: str) -> str:
    """读 collection 上该字段索引的 metric_type。

    必须动态读：检索时用的 metric_type 与建索引时必须一致，否则 Milvus 直接报
    `metric type not match`。硬编码 COSINE 就会在别人用 IP 建的库上炸掉。
    """
    try:
        client = get_client()
        for name in client.list_indexes(collection):
            info = client.describe_index(collection, name)
            if info.get("field_name") == field:
                return str(info.get("metric_type") or default)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 %s.%s 的索引参数失败：%s", collection, field, exc)
    return default


def ensure_collections(recreate: bool = False) -> list[str]:
    """建 collection + 索引 + BM25 Function（系统负责的"建表"入口）。"""
    from pymilvus import DataType, Function, FunctionType, MilvusClient

    client: MilvusClient = get_client()
    created: list[str] = []
    for name in COLLECTIONS:
        if client.has_collection(name):
            if not recreate:
                created.append(f"{name}: exists")
                continue
            client.drop_collection(name)

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(ID_FIELD, DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(TEXT_FIELD, DataType.VARCHAR, max_length=65535, enable_analyzer=True)
        schema.add_field(DENSE_FIELD, DataType.FLOAT_VECTOR, dim=settings.embedding_dim)
        # sparse 是 BM25 Function 的输出字段：不写入，由 Milvus 从 content 生成
        schema.add_field(SPARSE_FIELD, DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name=BM25_FUNCTION,
                input_field_names=[TEXT_FIELD],
                output_field_names=[SPARSE_FIELD],
                function_type=FunctionType.BM25,
            )
        )

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name=DENSE_FIELD,
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        index_params.add_index(
            field_name=SPARSE_FIELD,
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        client.create_collection(collection_name=name, schema=schema, index_params=index_params)
        created.append(f"{name}: created")
    return created


def upsert_documents(collection: str, rows: list[dict[str, Any]]) -> int:
    """写入文档。rows 只含 id / content / dense —— sparse 由 BM25 Function 生成。"""
    if not rows:
        return 0
    client = get_client()
    result = client.upsert(collection_name=collection, data=rows)
    # 未 flush 时数据仍在 growing segment：可能影响随后立刻发起的检索
    with contextlib.suppress(Exception):
        client.flush(collection_name=collection)
    return int(result["upsert_count"])


def count_documents(collection: str) -> int:
    """精确行数：count(*) 对未封存 segment 也准确，优于 get_collection_stats。"""
    client = get_client()
    rows = client.query(collection_name=collection, filter="", output_fields=["count(*)"])
    if not rows:
        return 0
    value = rows[0].get("count(*)") if isinstance(rows[0], dict) else None
    return int(value or 0)


def delete_by_ids(collection: str, ids: list[str]) -> None:
    if not ids:
        return
    quoted = ", ".join(f'"{i}"' for i in ids)
    get_client().delete(collection_name=collection, filter=f"id in [{quoted}]")


def hybrid_search(
    collection: str,
    *,
    dense: list[float],
    query: str,
    top_k: int = 10,
    id_prefix: str | None = None,
    rrf_k: int = 60,
    recall_multiplier: int = 3,
) -> list[dict[str, Any]]:
    """dense（向量）+ sparse（BM25 全文）双路召回，RRF 融合。

    这里**不用 Milvus 自带的 hybrid_search**，而是分别跑两路再自己算 RRF ——
    因为只有分路搜索才知道每条结果是"靠语义命中、靠关键词命中、还是两路都命中"，
    这个来源标注对调检索质量很关键（`retrieval` 字段）。

    参数都可从知识库配置里传：`rrf_k`（RRF 平滑常数）、`recall_multiplier`（每路召回倍数）。
    """
    client = get_client()
    expr = f'id like "{id_prefix}%"' if id_prefix else ""
    dense_metric = index_metric(collection, DENSE_FIELD, DEFAULT_DENSE_METRIC)
    sparse_metric = index_metric(collection, SPARSE_FIELD, DEFAULT_SPARSE_METRIC)
    recall = max(top_k * max(recall_multiplier, 1), top_k)

    lanes = [
        ("dense", dense, DENSE_FIELD, dense_metric, {"ef": 128}),
        # BM25 这一路传文本，不是向量：Milvus 用同一个 analyzer 算稀疏向量
        ("bm25", query, SPARSE_FIELD, sparse_metric, None),
    ]
    merged: dict[str, dict[str, Any]] = {}
    for label, data, field, metric, extra in lanes:
        params: dict[str, Any] = {"metric_type": metric}
        if extra:
            params["params"] = extra
        try:
            results = client.search(
                collection_name=collection,
                data=[data],
                anns_field=field,
                search_params=params,
                limit=recall,
                filter=expr,
                output_fields=[TEXT_FIELD],
            )
        except Exception as exc:  # noqa: BLE001 - 某一路挂了不该让整次检索失败
            logger.warning("%s 的 %s 路检索失败：%s", collection, label, exc)
            continue

        for rank, hit in enumerate(results[0] if results else [], start=1):
            doc_id = str(hit.get("id"))
            entry = merged.setdefault(
                doc_id,
                {
                    "id": doc_id,
                    "score": 0.0,
                    "text": (hit.get("entity") or {}).get(TEXT_FIELD, ""),
                    "source": collection,
                    "retrieval": [],
                    "_rank": {},
                },
            )
            # RRF：每条贡献 1/(k+rank)，两路都命中就自然叠加
            entry["score"] += 1.0 / (rrf_k + rank)
            entry["retrieval"].append(label)
            entry["_rank"][label] = rank

    hits = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]
    for hit in hits:
        lanes_hit = hit.pop("_rank")
        hit["retrieval"] = "both" if len(lanes_hit) > 1 else lanes_hit.popitem()[0]
    return hits
