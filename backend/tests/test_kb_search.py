"""知识库检索链路（D35）：多查询归并、来源标注、分数阈值。

不连 Milvus：把 hybrid_search 与 embedding 服务换成假的，专测归并逻辑。
"""

from __future__ import annotations

import pytest

from app.db.models import KnowledgeBase
from app.tools.kb_search import _merge_retrieval, search_knowledge_bases

RRF_K = 60
LANE_TOP1 = 1.0 / (RRF_K + 1)  # 单路第 1 名


def make_kb(**kwargs: object) -> KnowledgeBase:
    base: dict[str, object] = {
        "id": 1,
        "name": "JD 语料库",
        "collection": "jd",
        "top_k": 3,
        "rrf_k": RRF_K,
        "recall_multiplier": 3,
        "snippet_chars": 1200,
        "min_score": 0.0,
        "filter_fields": {},
        "is_enabled": True,
    }
    base.update(kwargs)
    return KnowledgeBase(**base)  # type: ignore[arg-type]


class FakeDB:
    def __init__(self, rows: list[KnowledgeBase]) -> None:
        self._rows = {row.id: row for row in rows}

    async def get(self, _model: object, key: int) -> KnowledgeBase | None:
        return self._rows.get(key)


class FakeEmbedding:
    async def embed_dense(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.fixture
def fake_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """doc1 两路都命中；doc2 只有 bm25；换个 query 再让 doc1 被语义命中一次。"""

    def fake_hybrid(
        _collection: str,
        *,
        dense: list[float],
        query: str,
        top_k: int = 10,
        id_prefix: str | None = None,
        rrf_k: int = RRF_K,
        recall_multiplier: int = 3,
    ) -> list[dict]:
        if query == "角度一":
            return [
                {
                    "id": "doc1",
                    "score": 2 / (rrf_k + 1),
                    "similarity": 0.91,
                    "text": "doc1 正文",
                    "retrieval": "both",
                },
                {
                    "id": "doc2",
                    "score": 1 / (rrf_k + 2),
                    "similarity": None,
                    "text": "doc2 正文",
                    "retrieval": "bm25",
                },
            ]
        return [
            {
                "id": "doc1",
                "score": 1 / (rrf_k + 1),
                "similarity": 0.88,
                "text": "doc1 正文",
                "retrieval": "dense",
            }
        ]

    monkeypatch.setattr("app.vector.milvus_client.hybrid_search", fake_hybrid)
    monkeypatch.setattr("app.vector.embedding.get_embedding_service", lambda: FakeEmbedding())


async def test_multi_query_accumulates_and_marks_sources(fake_retrieval: None) -> None:
    db = FakeDB([make_kb()])
    result = await search_knowledge_bases(
        db, queries=["角度一", "角度二"], knowledge_base_ids=[1]  # type: ignore[arg-type]
    )
    assert result["ok"] is True
    items = result["items"]
    assert [item["id"] for item in items] == ["doc1", "doc2"]

    doc1 = items[0]
    assert doc1["score"] == pytest.approx(3 * LANE_TOP1, abs=1e-4)  # 两路 + 两个角度
    assert doc1["retrieval"] == "both"
    assert doc1["matched_queries"] == ["角度一", "角度二"]

    doc2 = items[1]
    assert doc2["retrieval"] == "bm25"
    assert len(doc2["matched_queries"]) == 1


async def test_min_score_filters_on_similarity_not_rrf(fake_retrieval: None) -> None:
    """min_score 的语义是 **dense 原始相似度**下限，不是 RRF 分（RRF 只反映名次）。

    doc1 相似度 0.91 < 0.95 被滤掉；doc2 是纯 BM25 命中、没有相似度，
    不参与这条过滤——否则"关键词精确命中"会被静默丢掉。
    """
    db = FakeDB([make_kb(min_score=0.95)])
    result = await search_knowledge_bases(
        db, queries=["角度一"], knowledge_base_ids=[1]  # type: ignore[arg-type]
    )
    assert [item["id"] for item in result["items"]] == ["doc2"]
    assert result["items"][0]["similarity"] is None


async def test_similarity_is_reported(fake_retrieval: None) -> None:
    db = FakeDB([make_kb()])
    result = await search_knowledge_bases(
        db, queries=["角度一"], knowledge_base_ids=[1]  # type: ignore[arg-type]
    )
    assert result["items"][0]["similarity"] == pytest.approx(0.91)


async def test_chunks_of_one_document_collapse_to_the_best(monkeypatch: pytest.MonkeyPatch) -> None:
    """分块后同一岗位会占多个坑位：只留命中最高的那块，并报告总共命中几块。"""

    def fake_hybrid(_collection: str, *, query: str, **_kwargs: object) -> list[dict]:
        return [
            {"id": "jd_001#1", "score": 0.03, "similarity": 0.80, "text": "职责段", "retrieval": "dense"},
            {"id": "jd_001#2", "score": 0.05, "similarity": 0.93, "text": "要求段", "retrieval": "both"},
            {"id": "jd_002", "score": 0.04, "similarity": 0.88, "text": "别的岗位", "retrieval": "dense"},
        ]

    monkeypatch.setattr("app.vector.milvus_client.hybrid_search", fake_hybrid)
    monkeypatch.setattr("app.vector.embedding.get_embedding_service", lambda: FakeEmbedding())

    db = FakeDB([make_kb()])
    result = await search_knowledge_bases(
        db, queries=["角度一"], knowledge_base_ids=[1]  # type: ignore[arg-type]
    )
    assert [item["id"] for item in result["items"]] == ["jd_001#2", "jd_002"]
    assert result["items"][0]["parent"] == "jd_001"
    assert result["items"][0]["chunks_hit"] == 2
    assert result["items"][1]["chunks_hit"] == 1


async def test_snippet_chars_limits_text(fake_retrieval: None) -> None:
    db = FakeDB([make_kb(snippet_chars=4)])
    result = await search_knowledge_bases(
        db, queries=["角度一"], knowledge_base_ids=[1]  # type: ignore[arg-type]
    )
    assert result["items"][0]["text"] == "doc1"


async def test_total_char_budget_trims_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """观察结果会被序列化进模型上下文，超预算要按条裁剪，不能等它被硬截断成非法 JSON。"""
    from app.tools.kb_search import MAX_TOTAL_CHARS

    def fake_hybrid(_collection: str, **_kwargs: object) -> list[dict]:
        size = MAX_TOTAL_CHARS // 2
        return [
            {"id": f"doc{i}", "score": 1.0 - i * 0.1, "text": "x" * size, "retrieval": "both"}
            for i in range(3)
        ]

    monkeypatch.setattr("app.vector.milvus_client.hybrid_search", fake_hybrid)
    monkeypatch.setattr("app.vector.embedding.get_embedding_service", lambda: FakeEmbedding())

    db = FakeDB([make_kb(snippet_chars=MAX_TOTAL_CHARS)])
    result = await search_knowledge_bases(
        db, queries=["角度一"], knowledge_base_ids=[1]  # type: ignore[arg-type]
    )
    total = sum(len(item["text"]) for item in result["items"])
    assert total <= MAX_TOTAL_CHARS
    assert len(result["items"]) == 2  # 第三条放不下就不返回


async def test_top_k_is_the_final_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """页面配的 Top K 是**最终返回条数**：4 个角度 × 5 条候选也不该返回 20 条。"""

    def fake_hybrid(_collection: str, *, query: str, **_kwargs: object) -> list[dict]:
        return [
            {"id": f"{query}-{i}", "score": 1.0 - i * 0.01, "text": "x", "retrieval": "both"}
            for i in range(5)
        ]

    monkeypatch.setattr("app.vector.milvus_client.hybrid_search", fake_hybrid)
    monkeypatch.setattr("app.vector.embedding.get_embedding_service", lambda: FakeEmbedding())

    db = FakeDB([make_kb(top_k=3)])
    result = await search_knowledge_bases(
        db, queries=["a", "b", "c", "d"], knowledge_base_ids=[1]  # type: ignore[arg-type]
    )
    assert len(result["items"]) == 3


async def test_model_cannot_request_more_than_page_top_k(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型可以把条数要得更少，但不能超过页面配的上限。"""

    def fake_hybrid(_collection: str, *, query: str, **_kwargs: object) -> list[dict]:
        return [
            {"id": f"{query}-{i}", "score": 1.0 - i * 0.01, "text": "x", "retrieval": "both"}
            for i in range(10)
        ]

    monkeypatch.setattr("app.vector.milvus_client.hybrid_search", fake_hybrid)
    monkeypatch.setattr("app.vector.embedding.get_embedding_service", lambda: FakeEmbedding())

    db = FakeDB([make_kb(top_k=4)])
    result = await search_knowledge_bases(
        db,
        queries=["a"],
        knowledge_base_ids=[1],  # type: ignore[arg-type]
        top_k=99,
    )
    assert len(result["items"]) == 4


async def test_empty_queries_is_rejected(fake_retrieval: None) -> None:
    db = FakeDB([make_kb()])
    result = await search_knowledge_bases(
        db, queries=["   "], knowledge_base_ids=[1]  # type: ignore[arg-type]
    )
    assert result["ok"] is False
    assert "检索语句" in result["summary"]


async def test_missing_knowledge_base_is_rejected(fake_retrieval: None) -> None:
    db = FakeDB([make_kb()])
    result = await search_knowledge_bases(
        db, queries=["角度一"], knowledge_base_ids=[]  # type: ignore[arg-type]
    )
    assert result["ok"] is False


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ({"dense"}, "dense"),
        ({"bm25"}, "bm25"),
        ({"dense", "bm25"}, "both"),
        ({"both"}, "both"),
    ],
)
def test_merge_retrieval_labels(labels: set[str], expected: str) -> None:
    assert _merge_retrieval(labels) == expected
