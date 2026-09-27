"""FastAPI 入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)


async def sweep_interrupted_turns() -> int:
    """启动时收尾"上一次进程留下的半截回合"。

    为什么需要它：回合的收尾代码跑在进程内存里（`runs.status` / `tool_calls.status`
    由主循环最后统一改写）。只要进程在回合中途没了——**`--reload` 撞上正在跑的请求**、
    Ctrl+C、崩溃——数据库里就会留下永远是 `running` / `pending_approval` 的假记录：
    前端把它们当"还在跑"，看起来就是"每次调用工具都卡住"。
    启动时统一标成 interrupted，让状态诚实（真实数据一条不动，只改状态字段）。
    """
    from sqlalchemy import text

    from app.db.session import SessionLocal

    note = "后端进程中断（重启或热重载），这一回合没有跑完"
    try:
        async with SessionLocal() as db:
            calls = (
                await db.execute(
                    text(
                        "UPDATE tool_calls SET status='interrupted', error=:n"
                        " WHERE status IN ('running', 'pending_approval')"
                    ),
                    {"n": note},
                )
            ).rowcount
            runs = (
                await db.execute(
                    text(
                        "UPDATE runs SET status='failed', error=:n"
                        " WHERE status = 'running'"
                    ),
                    {"n": note},
                )
            ).rowcount
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - 清理失败绝不能挡住启动
        logger.warning("清理中断回合失败（忽略）：%s", exc)
        return 0
    if calls or runs:
        logger.info("清理中断回合：tool_calls %s 条、runs %s 条", calls, runs)
    return int(calls or 0) + int(runs or 0)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    setup_logging(settings.log_level)
    await sweep_interrupted_turns()
    logger.info(
        "启动完成：env=%s mysql=%s milvus=%s langfuse=%s",
        settings.app_env,
        settings.mysql_configured,
        settings.milvus_configured,
        settings.langfuse_enabled,
    )
    yield


app = FastAPI(
    title="简历诊断 Agent 平台",
    description="多智能体红蓝对抗的简历诊断与学习路线规划平台（Scene on Agent Platform）",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{settings.frontend_port}",
        f"http://127.0.0.1:{settings.frontend_port}",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", tags=["系统"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
