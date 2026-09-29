"""Skill 文件化迁移（SD16 / SD22）：正文搬到文件，表退回索引。

    ALTER TABLE skills DROP COLUMN instructions      -- 正文已搬到 backend/skill/<name>/SKILL.md
    ALTER TABLE skills DROP COLUMN kb_ids            -- ┐
    ALTER TABLE skills DROP COLUMN prompt_id         -- │ 旧架构（D25「Skill = 指令包 + 引用」）
    ALTER TABLE skills DROP COLUMN tool_ids          -- │ 留下的死列，现在的代码一处都不用
    ALTER TABLE skills DROP COLUMN output_schema     -- ┘
    ALTER TABLE skills MODIFY description VARCHAR(1024)   -- 对齐规范的 description 上限
    ALTER TABLE agents ADD COLUMN auto_distill TINYINT(1) NOT NULL DEFAULT 0  -- 蒸馏开关

迁移后 `skills` 只剩：id / name / description / is_enabled + 三个时间戳。

幂等，可重复执行。迁移本身**不动文件系统** —— 现有 skill 的正文请自己导出到
`backend/skill/<name>/SKILL.md`（首次跑启动对账时会自动补进表）。

用法（backend 目录下）：
    python -m scripts.migrate_skill_files --dry-run
    python -m scripts.migrate_skill_files
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: (表, 列, 判断存在与否的期望, DDL)
#: drop_column=True 表示"列存在时要删掉"
STEPS: list[tuple[str, str, str, bool]] = [
    ("skills", "instructions", "ALTER TABLE skills DROP COLUMN instructions", True),
    ("skills", "kb_ids", "ALTER TABLE skills DROP COLUMN kb_ids", True),
    ("skills", "prompt_id", "ALTER TABLE skills DROP COLUMN prompt_id", True),
    ("skills", "tool_ids", "ALTER TABLE skills DROP COLUMN tool_ids", True),
    ("skills", "output_schema", "ALTER TABLE skills DROP COLUMN output_schema", True),
    (
        "skills",
        "description",
        "ALTER TABLE skills MODIFY COLUMN description VARCHAR(1024) NOT NULL DEFAULT ''",
        False,
    ),
    (
        "agents",
        "auto_distill",
        "ALTER TABLE agents ADD COLUMN auto_distill TINYINT(1) NOT NULL DEFAULT 0",
        False,
    ),
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
        for table, column, ddl, drop_column in STEPS:
            exists = await has_column(db, table, column)
            # 删除类：列还在才要执行；新增/修改类：列不在就跳过（新增）或直接执行（修改）
            if drop_column and not exists:
                print(f"      {table}.{column} 已不存在")
                continue
            if not drop_column and "ADD COLUMN" in ddl and exists:
                print(f"      {table}.{column} 已存在")
                continue
            print(("      dry-run：" if dry_run else "      + ") + ddl)
            if not dry_run:
                await db.execute(text(ddl))
        if not dry_run:
            await db.commit()

    await engine.dispose()
    print("\n完成。" + ("（dry-run，未改动数据）" if dry_run else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Skill 文件化迁移（SD16 / SD22）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
