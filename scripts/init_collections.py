"""建 collection 与索引（系统负责的"建表"入口，幂等可重复执行）。

用法：
    python scripts/init_collections.py            # 已存在则跳过
    python scripts/init_collections.py --recreate # 删除重建（数据会丢）
"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 Milvus collection 与索引")
    parser.add_argument("--recreate", action="store_true", help="删除并重建 collection")
    args = parser.parse_args()

    if not settings.milvus_configured:
        print("未配置 MILVUS_HOST，请先在 .env 中填写。")
        return 2

    from app.vector.milvus_client import (
        COLLECTIONS,
        ensure_collections,
        parse_version,
        server_version,
    )

    try:
        version = server_version()
        print(f"Milvus 服务端版本：{version}")
    except Exception as exc:  # noqa: BLE001
        print(f"连接 Milvus 失败：{type(exc).__name__}: {exc}")
        return 1

    major_minor = parse_version(version)
    if major_minor and major_minor < (2, 4):
        print("⚠️  版本低于 2.4：稀疏向量与 hybrid search 不可用，请升级 Milvus。")

    try:
        for line in ensure_collections(recreate=args.recreate):
            print(" -", line)
    except Exception as exc:  # noqa: BLE001
        print(f"建表失败：{type(exc).__name__}: {exc}")
        return 1

    print(
        f"\n完成。collection：{' / '.join(COLLECTIONS)}"
        "（字段：id / content / dense / sparse[BM25 Function 自动生成]）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
