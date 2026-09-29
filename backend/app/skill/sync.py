"""`skills` 表与 `backend/skill` 目录的一致性（SD16 / SD17）。

分工：**文件是唯一真源，表是索引**。

  - 启动对账：目录有、表没有 → insert；description 漂移 → update
  - 写路径同步：蒸馏/新建刚落盘一个 skill → 立刻 upsert 那一行
  - **孤儿不做删除**：表里有、目录没有的行保留可见，由页面决定要不要清（SD22）
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Skill
from app.skill.store import SKILL_DIR, SkillFile, scan_skills

logger = logging.getLogger(__name__)


def is_orphan(name: str) -> bool:
    """表里有行、磁盘上没有目录。

    刻意**动态判定而不是落库**：多一个状态位就多一处会和现实脱节的地方。
    """
    try:
        return not (SKILL_DIR / name / "SKILL.md").is_file()
    except OSError:
        return True


async def reconcile(db: AsyncSession, *, dry_run: bool = False) -> dict[str, int]:
    """启动对账。返回统计，供启动日志与排查使用。"""
    files, problems = scan_skills()
    for problem in problems:
        logger.warning("skill 文件不合法，已跳过：%s", problem)

    rows = (await db.execute(select(Skill))).scalars().all()
    by_name = {row.name: row for row in rows}
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "invalid": len(problems)}

    for skill in files:
        row = by_name.get(skill.name)
        if row is None:
            stats["inserted"] += 1
            if not dry_run:
                db.add(Skill(name=skill.name, description=skill.description, is_enabled=True))
            continue
        if (row.description or "") != skill.description:
            stats["updated"] += 1
            if not dry_run:
                row.description = skill.description
                row.deleted_at = None
            continue
        if row.deleted_at is not None:
            # 文件还在，但表里被软删过 —— 以文件为准复活（文件是唯一真源）
            stats["updated"] += 1
            if not dry_run:
                row.deleted_at = None
            continue
        stats["unchanged"] += 1

    if not dry_run:
        await db.commit()
    return stats


async def upsert_one(db: AsyncSession, skill: SkillFile) -> Skill:
    """写路径同步：文件刚落盘，把表里那一行对齐（没有就插）。"""
    row = (await db.execute(select(Skill).where(Skill.name == skill.name))).scalar_one_or_none()
    if row is None:
        row = Skill(name=skill.name, description=skill.description, is_enabled=True)
        db.add(row)
    else:
        row.description = skill.description
        if row.deleted_at is not None:
            # 之前被停用/标记删过，但文件又出现了 —— 以文件为准复活
            row.deleted_at = None
    await db.commit()
    await db.refresh(row)
    return row
