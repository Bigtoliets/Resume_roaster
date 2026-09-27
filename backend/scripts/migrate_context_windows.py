"""长上下文治理迁移（D48）：配置字段 + 存档坐标 + 笔记表。

1. `model_configs.context_window`      模型最大上下文窗口（token）
2. `agents.context_threshold_pct`      超过窗口的这个百分比就换页（默认 80）
3. `messages` / `tool_calls` 各加 `window_index` + `seq`（存档坐标，指针 W2.M5 / W2.T3 靠它定位）
4. 建 `run_notes` 表（任务笔记，单独一张）

幂等，可重复执行。

用法（backend 目录下）：
    python -m scripts.migrate_context_windows --dry-run
    python -m scripts.migrate_context_windows
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COLUMNS: list[tuple[str, str, str]] = [
    ("model_configs", "context_window", "INT NOT NULL DEFAULT 65536"),
    ("agents", "context_threshold_pct", "INT NOT NULL DEFAULT 80"),
    ("messages", "window_index", "INT NOT NULL DEFAULT 0"),
    ("messages", "seq", "INT NOT NULL DEFAULT 0"),
    ("tool_calls", "window_index", "INT NOT NULL DEFAULT 0"),
    ("tool_calls", "seq", "INT NOT NULL DEFAULT 0"),
]


async def main_async(dry_run: bool) -> int:
    from sqlalchemy import text

    from app.core.config import settings
    from app.db.base import Base
    from app.db.models import RunNote  # noqa: F401  注册新表
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

    print("[1/2] 加列")
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

    print("[2/2] 建 run_notes 表")
    if dry_run:
        print("      dry-run：跳过 create_all")
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("      完成")

    await engine.dispose()
    print("\n完成。" + ("（dry-run，未改动数据）" if dry_run else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="长上下文治理迁移（D48）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
