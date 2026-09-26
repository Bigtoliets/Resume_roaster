"""校验 embedding 配置与代码路径：真的走 app.vector.embedding 发一次请求。

验证内容：
  1. .env 里的 provider / model / base_url 是否被正确加载（含 OPENAI_EMBED_* 别名）
  2. 接口是否可用、返回维度是否与 EMBEDDING_DIM 一致
  3. 分批逻辑是否生效（用一个超过批大小的输入测试）

用法：
    python scripts/check_embedding.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK = "[ OK ]"
FAIL = "[FAIL]"


def mask(value: str) -> str:
    if not value:
        return "(空)"
    return f"{value[:6]}...{value[-4:]}" if len(value) > 12 else "***"


async def main() -> int:
    try:
        from app.core.config import settings
        from app.vector.embedding import get_embedding_service
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} 无法导入后端模块 -> {exc}")
        print("       请在项目根目录执行，且已安装后端依赖（pip install -e backend）")
        return 2

    rows: list[tuple[str, str]] = []
    print(f"provider      : {settings.embedding_provider}")
    print(f"model         : {settings.embedding_model}")
    print(f"base_url      : {settings.embedding_base_url or '(空)'}")
    print(f"embeddings url: {settings.embedding_url or '(空)'}")
    print(f"api_key       : {mask(settings.embedding_api_key)}")
    print(f"dim / batch   : {settings.embedding_dim} / {settings.embedding_batch_size}")
    print()

    if not settings.embedding_remote_ready:
        print(f"{FAIL} 未配置 EMBEDDING_API_KEY 或 EMBEDDING_BASE_URL")
        return 1

    service = get_embedding_service()
    samples = [
        "大数据开发实习生：要求熟悉 Flink 状态管理、Kafka 顺序写、Hive 调优",
        "Java 后端开发：熟悉 Redis 缓存穿透/雪崩处理，有 JVM 调优经验",
    ]
    # 多给几条，用来验证分批（DashScope 单次上限 10 条）
    batch_probe = [f"{samples[i % 2]}（样本 {i}）" for i in range(12)]

    started = time.perf_counter()
    try:
        dense_list = await service.embed_dense(batch_probe)
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} embedding 调用失败 -> {type(exc).__name__}: {exc}")
        return 1
    elapsed = time.perf_counter() - started

    dim = len(dense_list[0]) if dense_list else 0
    rows.append(
        (
            OK if len(dense_list) == len(batch_probe) else FAIL,
            f"返回条数：{len(dense_list)}/{len(batch_probe)}",
        )
    )
    rows.append((OK if dim == settings.embedding_dim else FAIL, f"返回维度：{dim}（配置 {settings.embedding_dim}）"))
    rows.append((OK, "稀疏向量：不需要客户端提供（jd collection 的 sparse 由 Milvus BM25 Function 生成）"))
    rows.append((OK, f"耗时：{elapsed:.2f}s（含分批 {len(batch_probe)} 条）"))
    rows.append((OK, f"首条前 4 维：{[round(v, 4) for v in dense_list[0][:4]]}"))

    width = max(len(msg) for _, msg in rows)
    bar = "=" * (width + 8)
    print(bar)
    for status, msg in rows:
        print(f"{status} {msg}")
    print(bar)

    if any(status == FAIL for status, _ in rows):
        return 1
    print("\nembedding 对接完成。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
