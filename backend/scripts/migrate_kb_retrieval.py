"""知识库检索参数化迁移（D35）：给 knowledge_bases 加 4 个可调参数。

    rrf_k               RRF 平滑常数（默认 60）
    recall_multiplier   每路召回 top_k 的倍数（默认 3）
    snippet_chars       返回给模型的正文截断长度（默认 1200）
    min_score           RRF 分数下限，0 = 不过滤

幂等，可重复执行。

用法（backend 目录下）：
    python -m scripts.migrate_kb_retrieval --dry-run
    python -m scripts.migrate_kb_retrieval
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COLUMNS: list[tuple[str, str, str]] = [
    ("knowledge_bases", "rrf_k", "INT NOT NULL DEFAULT 60"),
    ("knowledge_bases", "recall_multiplier", "INT NOT NULL DEFAULT 3"),
    ("knowledge_bases", "snippet_chars", "INT NOT NULL DEFAULT 1200"),
    ("knowledge_bases", "min_score", "FLOAT NOT NULL DEFAULT 0"),
]


async def main_async(dry_run: bool) -> int:
    from sqlalchemy import text

    from app.core.config import settings
    from app.db.session import SessionLocal, engine

    if not settings.mysql_configured:
        print("未配置 MYSQL_HOST。")
        return 2

    async def has_column(db, table: str, column: str) -> bool:  # type: ignore[no-untyped-def]
        row = (
            await db.execute(
                text(
                    "SELECT 1 FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
                ),
                {"t": table, "c": column},
            )
        ).first()
        return row is not None

    async with SessionLocal() as db:
        for table, column, ddl in COLUMNS:
            if await has_column(db, table, column):
                print(f"      {table}.{column} 已存在")
                continue
            statement = f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
            print(("      dry-run：" if dry_run else "      + ") + statement)
            if not dry_run:
                await db.execute(text(statement))
        if not dry_run:
            await db.commit()

    await engine.dispose()
    print("\n完成。" + ("（dry-run，未改动数据）" if dry_run else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="知识库检索参数化迁移（D35）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
