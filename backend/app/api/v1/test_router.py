"""资源测试场：模型连通性、Prompt 试跑、Tool 执行、知识库检索测试。

原则：测试场与线上走**同一个执行入口**（`app.tools.registry.execute_tool`）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.security import decrypt
from app.db.models import KnowledgeBase, ModelConfig, Prompt, Tool
from app.llm.client import LLMClient, LLMError
from app.tools.registry import execute_tool, list_impls

router = APIRouter(tags=["测试场"])


class PromptTestIn(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)
    run: bool = False


class ToolTestIn(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolRunIn(BaseModel):
    """测试场：直接拿表单里的当前值跑，不要求先保存（D32.10）。"""

    impl_key: str
    args: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    prompt_id: int | None = None
    model_config_id: int | None = None
    timeout_s: int = 120
    name: str = "preview_tool"
    display_name: str = ""


class SearchTestIn(BaseModel):
    query: str = ""
    queries: list[str] = Field(default_factory=list)
    top_k: int | None = None
    id_prefix: str | None = None


@router.get("/tools/impls")
async def tool_impls() -> dict[str, Any]:
    """后端注册的工具实现清单（工具页选它 + 填配置）。"""
    return {"impls": list_impls()}


@router.post("/models/{item_id}/test")
async def test_model(item_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await db.get(ModelConfig, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    client = LLMClient(
        base_url=row.base_url,
        api_key=decrypt(row.api_key_cipher),
        model=row.model_name,
        params=row.default_params or {},
        timeout_s=min(row.timeout_s, 30),
    )
    started = time.perf_counter()
    try:
        reply = await client.complete([{"role": "user", "content": "回复「ok」两个字即可。"}])
    except LLMError as exc:
        return {"ok": False, "error": str(exc), "model": row.model_name}
    return {
        "ok": True,
        "model": row.model_name,
        "reply": reply.strip()[:100],
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


def _pick(messages: list[dict[str, Any]], role: str) -> str:
    return next(
        (str(m.get("content")) for m in messages if m.get("role") == role and m.get("content")),
        "",
    )


@router.post("/prompts/{item_id}/test")
async def test_prompt(
    item_id: int, payload: PromptTestIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    prompt_row = await db.get(Prompt, item_id)
    if prompt_row is None:
        raise HTTPException(status_code=404, detail="Prompt 不存在")

    def render(template: str) -> str:
        body = template
        for key, value in payload.variables.items():
            body = body.replace(
                "{{" + key + "}}",
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
            )
        return body

    messages = [
        {"role": "system", "content": render(_pick(prompt_row.messages or [], "system"))},
        {"role": "user", "content": render(_pick(prompt_row.messages or [], "user"))},
    ]
    result: dict[str, Any] = {"messages": messages}

    if payload.run:
        from app.core.config import settings

        model_row = await _default_model(db)
        base_params = dict(model_row.default_params or {}) if model_row else {}
        client = LLMClient(
            base_url=model_row.base_url if model_row else settings.deepseek_base_url,
            api_key=decrypt(model_row.api_key_cipher) if model_row else settings.deepseek_api_key,
            model=model_row.model_name if model_row else settings.default_chat_model,
            params={**base_params, **(prompt_row.params or {})},
        )
        started = time.perf_counter()
        try:
            result["output"] = await client.complete(messages)
        except LLMError as exc:
            result["error"] = str(exc)
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return result


@router.post("/tools/{item_id}/test")
async def test_tool(
    item_id: int, payload: ToolTestIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = await db.get(Tool, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tool 不存在")

    fallback: LLMClient | None = None
    model_row = await _default_model(db)
    if row.model_config_id:
        model_row = await db.get(ModelConfig, row.model_config_id) or model_row
    if model_row is not None:
        fallback = LLMClient(
            base_url=model_row.base_url,
            api_key=decrypt(model_row.api_key_cipher),
            model=model_row.model_name,
            params=model_row.default_params or {},
        )

    started = time.perf_counter()
    output = await execute_tool(db, tool=row, arguments=payload.arguments, fallback_llm=fallback)  # type: ignore[arg-type]
    return {
        "ok": bool(output.get("ok", True)),
        "output": output,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


@router.post("/tools/test")
async def run_tool_preview(
    payload: ToolRunIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """按表单当前值直接执行一次，不写库 —— 保存之前就能试到通。"""
    draft = Tool(
        name=payload.name or "preview_tool",
        display_name=payload.display_name or payload.name or "预览",
        impl_key=payload.impl_key,
        prompt_id=payload.prompt_id,
        model_config_id=payload.model_config_id,
        timeout_s=payload.timeout_s,
        impl_config=payload.config or {},
        params_schema={},
        returns_schema={},
        is_enabled=True,
    )
    fallback: LLMClient | None = None
    model_row = await _default_model(db)
    if payload.model_config_id:
        model_row = await db.get(ModelConfig, payload.model_config_id) or model_row
    if model_row is not None:
        fallback = LLMClient(
            base_url=model_row.base_url,
            api_key=decrypt(model_row.api_key_cipher),
            model=model_row.model_name,
            params=model_row.default_params or {},
        )
    started = time.perf_counter()
    output = await execute_tool(
        db, tool=draft, arguments=payload.args, fallback_llm=fallback  # type: ignore[arg-type]
    )
    return {
        "ok": bool(output.get("ok", True)),
        "output": output,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


@router.post("/knowledge-bases/{item_id}/search")
async def test_knowledge_base(
    item_id: int, payload: SearchTestIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    from app.tools.kb_search import search_knowledge_bases

    row = await db.get(KnowledgeBase, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    queries = [q for q in (payload.queries or [payload.query]) if q and q.strip()]
    if not queries:
        raise HTTPException(status_code=422, detail="至少要给一条检索语句")
    started = time.perf_counter()
    try:
        result = await search_knowledge_bases(
            db,
            queries=queries,
            top_k=payload.top_k or row.top_k,
            knowledge_base_ids=[row.id],
            id_prefix=payload.id_prefix or "",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"检索失败：{type(exc).__name__}: {exc}") from exc
    return {
        "collection": row.collection,
        "count": len(result.get("items") or []),
        "hits": result.get("items") or [],
        "summary": result.get("summary", ""),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


async def _default_model(db: AsyncSession) -> ModelConfig | None:
    stmt = (
        select(ModelConfig)
        .where(ModelConfig.deleted_at.is_(None), ModelConfig.is_enabled.is_(True))
        .order_by(ModelConfig.is_default.desc(), ModelConfig.id.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
