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


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    setup_logging(settings.log_level)
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
