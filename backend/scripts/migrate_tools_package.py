"""工具包迁移（D32）：把 `tools` 表迁到"页面 JSON + 代码实现"的最终形态。

幂等，可重复执行。做四件事：
1. 加 `impl_key`（指向 `app/tools/<impl_key>.py`），并把旧的 `impl_config.capability` 迁过来
2. 保证 `params_schema` / `returns_schema` 两列存在（旧的 `param_overrides` 会被改名回来）
3. 删掉由代码决定的冗余列：`kind` / `impl_type` / `max_rounds`
4. 用代码里的默认值给**空**的 `params_schema` / `returns_schema` / `impl_config` 兜底
   —— 只填空值，不覆盖页面上已经填过的东西

用法（backend 目录下）：
    python -m scripts.migrate_tools_package --dry-run
    python -m scripts.migrate_tools_package
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: 需要保证存在的列
REQUIRED_COLUMNS: list[tuple[str, str, str]] = [
    ("tools", "impl_key", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("tools", "params_schema", "JSON NULL"),
    ("tools", "returns_schema", "JSON NULL"),
    # D36：新增的可调行为 —— 工具失败重试次数、同一轮多工具是否并发
    ("tools", "retry_times", "INT NOT NULL DEFAULT 1"),
    ("agents", "parallel_tools", "TINYINT(1) NOT NULL DEFAULT 1"),
]

#: 改由代码声明、不再入库的列
DROPPED_COLUMNS: list[tuple[str, str]] = [
    ("tools", "kind"),
    ("tools", "impl_type"),
    ("tools", "max_rounds"),
]

#: 历史名字 → 现在用的名字
RENAMES: list[tuple[str, str, str]] = [("tools", "param_overrides", "params_schema")]


async def main_async(dry_run: bool) -> int:
    from sqlalchemy import select, text

    from app.core.config import settings
    from app.db.models import Tool
    from app.db.session import SessionLocal, engine
    from app.tools.registry import get_impl

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
        print("[1/4] 列调整")
        for table, old, new in RENAMES:
            if not await has_column(db, table, old):
                continue
            if await has_column(db, table, new):
                print(f"      {table}.{new} 已存在，跳过 {old} 改名")
                continue
            statement = f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"
            print(("      dry-run：" if dry_run else "      ~ ") + statement)
            if not dry_run:
                await db.execute(text(statement))
        for table, column, ddl in REQUIRED_COLUMNS:
            if await has_column(db, table, column):
                continue
            statement = f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
            print(("      dry-run：" if dry_run else "      + ") + statement)
            if not dry_run:
                await db.execute(text(statement))
        for table, column in DROPPED_COLUMNS:
            if not await has_column(db, table, column):
                continue
            statement = f"ALTER TABLE {table} DROP COLUMN {column}"
            print(("      dry-run：" if dry_run else "      - ") + statement)
            if not dry_run:
                await db.execute(text(statement))
        if not dry_run:
            await db.commit()

    print("[2/4] 数据回填")
    if dry_run:
        print("      dry-run：跳过（列还没就绪）")
        await engine.dispose()
        print("\n完成。（dry-run，未改动数据）")
        return 0

    async with SessionLocal() as db:
        for row in (await db.execute(select(Tool))).scalars().all():
            touched: list[str] = []

            if not (row.impl_key or "").strip():
                config = dict(row.impl_config or {})
                row.impl_key = str(config.pop("capability", "") or "") or row.name
                row.impl_config = config
                touched.append("impl_key")

            impl = get_impl(row.impl_key)
            if impl is not None:
                if not row.params_schema:
                    row.params_schema = impl.default_params_schema
                    touched.append("params_schema")
                if not row.returns_schema:
                    row.returns_schema = impl.default_returns_schema
                    touched.append("returns_schema")
                missing = {
                    key: value
                    for key, value in impl.default_config.items()
                    if key not in (row.impl_config or {})
                }
                if missing:
                    row.impl_config = {**missing, **(row.impl_config or {})}
                    touched.append("impl_config")

            # 旧列语义是"整份结构定义"，不是当前契约，直接清空等上面兜底
            overrides = row.params_schema or {}
            if isinstance(overrides, dict) and "type" in overrides and "impl_key" in touched:
                row.params_schema = {}
                touched.append("params_schema(重置)")

            if touched:
                print(f"      #{row.id} {row.name}：{'、'.join(touched)}")
        await db.commit()

    await engine.dispose()
    print("\n[3/4] 完成。[4/4] 提示：代码里的实现清单见 `backend/app/tools/`。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="工具包迁移（D32）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
