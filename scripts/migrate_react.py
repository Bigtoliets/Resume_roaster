"""ReAct 改造迁移（D31）：补列、建 tool_calls、下架场景编排。

做四件事：
1. 建缺失的表（新增 `tool_calls`）
2. 给 `tools` / `agents` 补 ReAct 需要的列
3. **删除场景编排的两张表**（`scene_configs` / `scene_node_bindings`）——节点/覆盖/场景档概念已废弃
4. 把历史行可能存在的 NULL 补成默认值

用法：
    python scripts/migrate_react.py            # 执行
    python scripts/migrate_react.py --dry-run  # 只看会做什么
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


NEW_COLUMNS: list[tuple[str, str, str]] = [
    ("tools", "display_name", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("tools", "kind", "VARCHAR(16) NOT NULL DEFAULT 'builtin'"),
    ("tools", "prompt_id", "BIGINT NULL"),
    ("tools", "model_config_id", "BIGINT NULL"),
    ("tools", "requires_approval", "TINYINT(1) NOT NULL DEFAULT 0"),
    ("tools", "max_rounds", "INT NOT NULL DEFAULT 6"),
    ("tools", "timeout_s", "INT NOT NULL DEFAULT 120"),
    ("agents", "tool_ids", "JSON NULL"),
    ("agents", "max_rounds", "INT NOT NULL DEFAULT 12"),
]

DROP_TABLES = ["scene_node_bindings", "scene_configs"]

NULL_FIXES: list[tuple[str, str, str]] = [
    ("sessions", "summary", "''"),
    ("sessions", "last_route", "''"),
    ("sessions", "summary_upto_message_id", "0"),
    ("agents", "tool_ids", "'[]'"),
]


async def main_async(dry_run: bool) -> int:
    from sqlalchemy import text

    from app.core.config import settings
    from app.db.base import Base
    from app.db.models import ToolCall  # noqa: F401  注册新表
    from app.db.session import SessionLocal, engine

    if not settings.mysql_configured:
        print("未配置 MYSQL_HOST。")
        return 2

    async def has_column(db, table: str, column: str) -> bool:
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

    async def has_table(db, table: str) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT 1 FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
                ),
                {"t": table},
            )
        ).first()
        return row is not None

    print("[1/4] 建缺失的表（新增 tool_calls）")
    if dry_run:
        print("      dry-run：跳过 create_all")
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("      完成")

    print("[2/4] 补 ReAct 需要的列")
    async with SessionLocal() as db:
        for table, column, ddl in NEW_COLUMNS:
            if not await has_table(db, table):
                print(f"      跳过 {table}（表不存在）")
                continue
            if await has_column(db, table, column):
                continue
            statement = f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
            if dry_run:
                print(f"      dry-run：{statement}")
                continue
            await db.execute(text(statement))
            print(f"      + {table}.{column}")
        if not dry_run:
            await db.commit()

    print("[3/4] 下架场景编排（删除废弃表）")
    async with SessionLocal() as db:
        for table in DROP_TABLES:
            if not await has_table(db, table):
                print(f"      {table} 已不存在")
                continue
            if dry_run:
                print(f"      dry-run：DROP TABLE {table}")
                continue
            await db.execute(text(f"DROP TABLE {table}"))
            print(f"      - 删除表 {table}")
        if not dry_run:
            await db.commit()

    print("[4/4] 补齐历史行的 NULL")
    async with SessionLocal() as db:
        for table, column, value in NULL_FIXES:
            if not await has_table(db, table) or not await has_column(db, table, column):
                continue
            statement = f"UPDATE {table} SET {column} = {value} WHERE {column} IS NULL"
            if dry_run:
                print(f"      dry-run：{statement}")
                continue
            result = await db.execute(text(statement))
            print(f"      {table}.{column} 补齐 {result.rowcount} 行")
        if not dry_run:
            await db.commit()

    await engine.dispose()
    print("\n完成。" + ("（dry-run，未改动数据）" if dry_run else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ReAct 改造迁移")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
