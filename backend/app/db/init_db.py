"""开发期建表：直接按 ORM 元数据创建（正式迁移走 Alembic）。"""

from __future__ import annotations

import asyncio

from app.core.config import settings

# 导入以注册全部表
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import engine


async def create_all(drop: bool = False) -> None:
    if not settings.mysql_configured:
        raise SystemExit("MySQL 未配置：请在 .env 中填写 MYSQL_HOST 等字段")
    async with engine.begin() as conn:
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


if __name__ == "__main__":
    import sys

    asyncio.run(create_all(drop="--drop" in sys.argv))
    print("表结构已创建。")
