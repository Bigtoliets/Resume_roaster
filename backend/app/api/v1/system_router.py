"""系统状态与初始化。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.db.models import Agent, KnowledgeBase, ModelConfig, Prompt, Skill, Tool
from app.obs.langfuse import client_status as langfuse_client_status
from app.tools.registry import list_impls

router = APIRouter(tags=["系统"])


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "mysql_configured": settings.mysql_configured,
        "milvus_configured": settings.milvus_configured,
        "langfuse_configured": settings.langfuse_enabled,
    }


@router.get("/system/status")
async def system_status(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    status: dict[str, Any] = {
        "mysql": {"configured": settings.mysql_configured, "ok": False, "error": ""},
        "milvus": {"configured": settings.milvus_configured, "ok": False, "version": "", "error": ""},
        # 真去拿一次客户端：只报 configured 会在"SDK 没装 / 服务不可达"时骗人
        "langfuse": langfuse_client_status(),
        "embedding": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "dim": settings.embedding_dim,
        },
        "impls": [item["impl_key"] for item in list_impls()],
        "counts": {},
    }

    try:
        await db.execute(text("SELECT 1"))
        status["mysql"]["ok"] = True
    except Exception as exc:  # noqa: BLE001
        status["mysql"]["error"] = f"{type(exc).__name__}: {exc}"

    if settings.milvus_configured:
        try:
            from app.vector.milvus_client import COLLECTIONS, parse_version, server_version

            version = server_version()
            status["milvus"].update({"ok": True, "version": version, "collections": list(COLLECTIONS)})
            major_minor = parse_version(version)
            if major_minor and major_minor < (2, 4):
                status["milvus"]["warning"] = "稀疏向量与 hybrid search 需要 Milvus ≥2.4"
        except Exception as exc:  # noqa: BLE001
            status["milvus"].update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    if status["mysql"]["ok"]:
        for label, model in (
            ("models", ModelConfig),
            ("prompts", Prompt),
            ("tools", Tool),
            ("knowledge_bases", KnowledgeBase),
            ("skills", Skill),
            ("agents", Agent),
        ):
            count = (
                await db.execute(
                    select(func.count()).select_from(model).where(model.deleted_at.is_(None))
                )
            ).scalar()
            status["counts"][label] = int(count or 0)
    return status


@router.post("/system/milvus/init")
async def init_milvus(recreate: bool = False) -> dict[str, Any]:
    """建 collection 与索引（幂等）。"""
    try:
        from app.vector.milvus_client import ensure_collections

        created = ensure_collections(recreate=recreate)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"created": created}
