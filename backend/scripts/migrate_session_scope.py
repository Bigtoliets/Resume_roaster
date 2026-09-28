"""会话级指针与笔记覆盖迁移（D50 / D62 / D63）：给 run_notes 加 pointer_scope 列。

为什么需要这一列：D50 把存档指针的作用域从"回合"改成"会话"（窗口号不再每回合从
W1 重来）。改造前生成的笔记里，指针是**回合级编号** —— 在会话作用域下同一会话必然
有多行 `W1`，回查时会命中多条、只能报 ambiguous。

所以注入模型时只认 `pointer_scope = "session"` 的行，老行保持 `"run"`（默认值），
从此既不注入、也不再展示。旧行的内容一个字都不动 —— 这是 D62"不回填"的落地方式：
不迁移数据，只标记作用域。

幂等，可重复执行。

用法（backend 目录下）：
    python -m scripts.migrate_session_scope --dry-run
    python -m scripts.migrate_session_scope
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COLUMNS: list[tuple[str, str, str]] = [
    ("run_notes", "pointer_scope", "VARCHAR(8) NOT NULL DEFAULT 'run'"),
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

        if not dry_run:
            total = (await db.execute(text("SELECT COUNT(*) FROM run_notes"))).scalar() or 0
            legacy = (
                await db.execute(
                    text("SELECT COUNT(*) FROM run_notes WHERE pointer_scope = 'run'")
                )
            ).scalar() or 0
            print(f"\n      run_notes 共 {total} 行，其中标记为回合级（不注入）{legacy} 行。")
            print("      这些老笔记不会被删，也不会被注入；新回合会就地覆盖成会话级笔记。")

    await engine.dispose()
    print("\n完成。" + ("（dry-run，未改动数据）" if dry_run else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="会话级指针与笔记覆盖迁移（D50/D62/D63）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
