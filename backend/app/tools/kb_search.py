"""知识库检索：多查询 × 双路召回 × RRF 融合（D35）。

一次调用可以传多个检索语句（`queries`），覆盖不同角度——比如
「岗位硬性要求」「技术栈考点」「项目深度」——同一个文档被越多角度命中，
累计分数越高，排在越前面。每条结果都带 `retrieval` 标注它是靠
语义（dense）/ 关键词（bm25）/ 两路都命中（both）被召回的。

检索行为的可调参数全部来自**知识库配置**（页面可改）：
    top_k               **最终返回条数的上限**（页面配的值；多角度只提升召回质量，不放大条数）
    rrf_k               RRF 平滑常数，越大越平缓（默认 60）
    recall_multiplier   每路预先召回 top_k 的多少倍（默认 3）
    snippet_chars       返回给模型的正文截断长度（默认 1200）
    min_score           RRF 分数下限，低于它的结果不返回（默认 0 = 不过滤）
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolContext
from app.tools.registry import tool

#: 多角度检索时最终返回条数的上限，避免把模型上下文塞满
MAX_ITEMS = 50

#: 返回正文的总字符预算：观察结果最终会被 JSON 序列化进模型上下文，
#: 超了就会被硬截断成非法 JSON，所以在这里先按预算裁条数。
MAX_TOTAL_CHARS = 24000


def _merge_retrieval(labels: set[str]) -> str:
    if "both" in labels or {"dense", "bm25"} <= labels:
        return "both"
    return next(iter(labels)) if labels else "dense"


async def search_knowledge_bases(
    db: AsyncSession,
    *,
    queries: list[str],
    knowledge_base_ids: list[int],
    top_k: int | None = None,
    id_prefix: str = "",
) -> dict[str, Any]:
    """多查询混合检索本体；工具与「知识库测试场」共用这一份实现。"""
    from app.db.models import KnowledgeBase
    from app.vector.embedding import get_embedding_service
    from app.vector.milvus_client import hybrid_search

    cleaned = [q.strip() for q in queries if q and q.strip()]
    if not cleaned:
        return {"ok": False, "summary": "没有提供检索语句（queries 为空）", "items": []}
    if not knowledge_base_ids:
        return {"ok": False, "summary": "没有指定知识库", "items": []}

    rows = [await db.get(KnowledgeBase, kb_id) for kb_id in knowledge_base_ids]
    kbs = [row for row in rows if row is not None and row.is_enabled and row.deleted_at is None]
    if not kbs:
        return {"ok": False, "summary": "绑定的知识库不存在或已停用", "items": []}

    # 页面配的 top_k 是上限：模型可以要更少，不能要更多（D32.4 配置优先）
    page_limit = max(int(kb.top_k or 10) for kb in kbs)
    requested = int(top_k) if isinstance(top_k, (int, float)) and top_k else page_limit
    per_query = max(min(requested, page_limit, MAX_ITEMS), 1)

    # 一次把所有查询语句编码完，避免逐条打 embedding 接口
    dense_list = await get_embedding_service().embed_dense(cleaned)

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    detail: list[dict[str, Any]] = []
    for kb in kbs:
        per_kb = 0
        for query, dense in zip(cleaned, dense_list, strict=True):
            hits = hybrid_search(
                kb.collection,
                dense=dense,
                query=query,
                top_k=per_query,
                id_prefix=id_prefix or (kb.filter_fields or {}).get("id_prefix") or None,
                rrf_k=int(kb.rrf_k or 60),
                recall_multiplier=int(kb.recall_multiplier or 3),
            )
            per_kb += len(hits)
            for hit in hits:
                key = (kb.collection, str(hit["id"]))
                entry = merged.setdefault(
                    key,
                    {
                        "knowledge_base": kb.name,
                        "collection": kb.collection,
                        "id": hit["id"],
                        "_score": 0.0,
                        "_labels": set(),
                        "matched_queries": [],
                        "_text": str(hit.get("text") or ""),
                        "_snippet_chars": int(kb.snippet_chars or 1200),
                        "_min_score": float(kb.min_score or 0.0),
                    },
                )
                entry["_score"] += float(hit["score"])
                entry["_labels"].add(str(hit.get("retrieval") or "dense"))
                if query not in entry["matched_queries"]:
                    entry["matched_queries"].append(query)
        detail.append(
            {
                "knowledge_base": kb.name,
                "collection": kb.collection,
                "queries": len(cleaned),
                "hits": per_kb,
            }
        )

    items: list[dict[str, Any]] = []
    for entry in merged.values():
        if entry["_score"] < entry["_min_score"]:
            continue
        items.append(
            {
                "knowledge_base": entry["knowledge_base"],
                "collection": entry["collection"],
                "id": entry["id"],
                "score": round(entry["_score"], 4),
                "retrieval": _merge_retrieval(entry["_labels"]),
                "matched_queries": entry["matched_queries"],
                "text": entry["_text"][: entry["_snippet_chars"]],
            }
        )
    items.sort(key=lambda item: item["score"], reverse=True)

    # 最终条数就是顶部那个上限，不随角度数放大
    cap = per_query
    kept: list[dict[str, Any]] = []
    budget = MAX_TOTAL_CHARS
    for item in items[:cap]:
        cost = len(item["text"])
        if kept and cost > budget:
            break
        kept.append(item)
        budget -= cost
    items = kept
    if not items:
        return {
            "ok": False,
            "summary": "没有命中（可能是 collection 为空、id 前缀过滤太严，或 min_score 设得太高）",
            "items": [],
            "detail": detail,
        }
    summary = "；".join(
        f"{d['knowledge_base']} {d['queries']} 个角度命中 {d['hits']} 次" for d in detail
    )
    return {"ok": True, "summary": summary, "items": items, "detail": detail}


@tool(
    impl_key="kb_search",
    title="知识库检索",
    description=(
        "在绑定的知识库里做语义 + 关键词混合检索。一次可以传多条检索语句，"
        "从不同角度覆盖（如岗位硬性要求 / 技术栈考点 / 项目深度），"
        "被多个角度同时命中的文档会排在前面。"
    ),
    config={"knowledge_base_ids": [], "id_prefix": ""},
    params_schema={
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-5 条检索语句，每条一个角度；比单条长句命中率更高",
            },
            "query": {
                "type": "string",
                "description": "单条检索语句（兼容写法；和 queries 二选一）",
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "summary": {"type": "string"},
            "data": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "每条含 id / score / retrieval(dense|bm25|both) / matched_queries / text",
                    }
                },
            },
        },
    },
)
async def run(input: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    args = input.get("args") or {}
    config = input.get("config") or {}

    queries = args.get("queries")
    if isinstance(queries, str):
        queries = [queries]
    if not queries:
        single = str(args.get("query") or "").strip()
        queries = [single] if single else []
    if not queries:
        return {
            "ok": False,
            "summary": "缺少必填参数：queries（字符串数组）或 query（单条）",
            "error": "missing_arguments",
        }

    kb_ids = [int(item) for item in (config.get("knowledge_base_ids") or [])]
    if not kb_ids:
        return {
            "ok": False,
            "summary": "这个工具还没绑定知识库：在「配置 JSON」里填 knowledge_base_ids",
            "error": "config_incomplete",
        }

    return await search_knowledge_bases(
        ctx.db,
        queries=[str(q) for q in queries][:5],
        knowledge_base_ids=kb_ids,
        # 返回条数**只由知识库页面的「默认 Top K」决定**：模型看不到也不该改这个参数，
        # 否则它每次都自己传一个值（实测一直是 8），页面配置形同虚设。
        id_prefix=str(config.get("id_prefix") or ""),
    )
