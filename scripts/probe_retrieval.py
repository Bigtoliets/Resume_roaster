"""检索命中测试：给定 query，看 jd collection 的混合检索结果与分数。

用途：调语料、验证 embedding 是否真的可用、演示前确认检索不空。

用法：
    python scripts/probe_retrieval.py "Flink checkpoint 状态管理与反压"
    python scripts/probe_retrieval.py "缓存穿透 分布式锁" --top-k 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def run(query: str, collections: list[str], top_k: int, prefix: str | None) -> int:
    from app.core.config import settings
    from app.vector.embedding import get_embedding_service
    from app.vector.milvus_client import hybrid_search

    if not settings.milvus_configured:
        print("未配置 MILVUS_HOST。")
        return 2

    service = get_embedding_service()
    provider = service.resolve_provider()
    dense_list = await service.embed_dense([query])
    dense = dense_list[0]
    print(f"provider = {provider} | dense 维度 = {len(dense)} | sparse 由 Milvus BM25 生成")
    if provider == "fake":
        print("⚠️  fake 向量只是占位，命中结果不具语义意义。")

    overall_ok = True
    for name in collections:
        hits = hybrid_search(name, dense=dense, query=query, top_k=top_k, id_prefix=prefix)
        print(f"\n--- {name}（{len(hits)} 条命中）---")
        if not hits:
            print("  没有命中：collection 可能为空，或 id 前缀过滤太严。")
            overall_ok = False
        for hit in hits:
            preview = hit["text"].replace("\n", " ")[:70]
            # rrf 分只反映名次（量级 0.0x）；sim 才是 dense 路的真实相似度，
            # 调相关度阈值看的是后者（纯关键词命中没有 sim）。
            similarity = hit.get("similarity")
            shown = f"{similarity:.3f}" if isinstance(similarity, float) else "  -  "
            print(f"  rrf={hit['score']:.4f}  sim={shown}  {hit['id']:<22} {preview}…")
    return 0 if overall_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="混合检索命中测试")
    parser.add_argument("query", help="检索语句")
    parser.add_argument(
        "--collection", action="append", choices=["jd"], help="可重复指定，默认查 jd"
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--id-prefix", default=None, help="按岗位族前缀收窄，如 bigdata_")
    args = parser.parse_args()
    return asyncio.run(run(args.query, args.collection or ["jd"], args.top_k, args.id_prefix))


if __name__ == "__main__":
    sys.exit(main())
