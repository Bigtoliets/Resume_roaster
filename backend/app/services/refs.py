"""资源引用关系与版本快照（D31 版）。

引用图（决定删除保护）：
    Agent  → model_config / prompt / skills / tools
    Tool   → model_config / prompt / knowledge_bases（写在 impl_config 里）
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Agent,
    KnowledgeBase,
    ModelConfig,
    Prompt,
    ResourceVersion,
    Skill,
    Tool,
)

RESOURCE_TYPES: dict[str, type] = {
    "models": ModelConfig,
    "prompts": Prompt,
    "tools": Tool,
    "knowledge-bases": KnowledgeBase,
    "skills": Skill,
    "agents": Agent,
}


def _jsonable(value: Any) -> Any:
    """把 ORM 值转成可写入 JSON 列的形态（datetime / Decimal / bytes 等）。"""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


async def _all(db: AsyncSession, model: type) -> list[Any]:
    return list(
        (await db.execute(select(model).where(model.deleted_at.is_(None)))).scalars().all()
    )


async def find_referencing_agents(
    db: AsyncSession, resource_type: str, resource_id: int
) -> list[dict[str, Any]]:
    """返回引用了该资源的配置项（Agent / Tool），用于删除保护。"""
    hits: list[dict[str, Any]] = []

    for agent in await _all(db, Agent):
        if resource_type == "models" and agent.model_config_id == resource_id:
            hits.append({"type": "Agent", "id": agent.id, "name": agent.name})
        elif resource_type == "prompts" and agent.prompt_id == resource_id:
            hits.append({"type": "Agent", "id": agent.id, "name": agent.name})
        elif resource_type == "skills" and resource_id in (agent.skill_ids or []):
            hits.append({"type": "Agent", "id": agent.id, "name": agent.name})
        elif resource_type == "tools" and resource_id in (agent.tool_ids or []):
            hits.append({"type": "Agent", "id": agent.id, "name": agent.name})

    for tool in await _all(db, Tool):
        if resource_type == "models" and tool.model_config_id == resource_id:
            hits.append({"type": "Tool", "id": tool.id, "name": tool.name})
        elif resource_type == "prompts" and tool.prompt_id == resource_id:
            hits.append({"type": "Tool", "id": tool.id, "name": tool.name})
        elif resource_type == "knowledge-bases" and resource_id in (
            (tool.impl_config or {}).get("knowledge_base_ids") or []
        ):
            hits.append({"type": "Tool", "id": tool.id, "name": tool.name})

    return hits


async def snapshot_agent(db: AsyncSession, agent: Agent) -> dict[str, Any]:
    """把 Agent 及其引用的全部资源打成不可变 JSON 快照。"""

    async def _load(model: type, item_id: int | None) -> dict[str, Any] | None:
        if not item_id:
            return None
        obj = await db.get(model, item_id)
        if obj is None:
            return None
        # server_default 字段在 flush 后仍未加载，先 refresh 避免同步惰性加载报错
        await db.refresh(obj)
        return {
            column.key: _jsonable(getattr(obj, column.key))
            for column in obj.__table__.columns
            if column.key != "api_key_cipher"
        }

    async def _load_many(model: type, ids: list[int]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item_id in ids or []:
            loaded = await _load(model, item_id)
            if loaded:
                out.append(loaded)
        return out

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "persona": agent.persona,
            "max_rounds": agent.max_rounds,
            "params_override": agent.params_override,
            "skill_ids": agent.skill_ids or [],
            "tool_ids": agent.tool_ids or [],
        },
        "model_config": await _load(ModelConfig, agent.model_config_id),
        "prompt": await _load(Prompt, agent.prompt_id),
        "skills": await _load_many(Skill, agent.skill_ids or []),
        "tools": await _load_many(Tool, agent.tool_ids or []),
    }


async def unbind_references(
    db: AsyncSession, resource_type: str, resource_id: int
) -> list[dict[str, Any]]:
    """把引用了该资源的地方解绑（用于"解绑并删除"），返回解绑明细。"""
    unbound: list[dict[str, Any]] = []

    for agent in await _all(db, Agent):
        changed = False
        if resource_type == "models" and agent.model_config_id == resource_id:
            agent.model_config_id = None
            changed = True
        elif resource_type == "prompts" and agent.prompt_id == resource_id:
            agent.prompt_id = None
            changed = True
        elif resource_type == "skills" and resource_id in (agent.skill_ids or []):
            agent.skill_ids = [i for i in (agent.skill_ids or []) if i != resource_id]
            changed = True
        elif resource_type == "tools" and resource_id in (agent.tool_ids or []):
            agent.tool_ids = [i for i in (agent.tool_ids or []) if i != resource_id]
            changed = True
        if changed:
            unbound.append({"type": "Agent", "id": agent.id, "name": agent.name})

    for tool in await _all(db, Tool):
        changed = False
        if resource_type == "models" and tool.model_config_id == resource_id:
            tool.model_config_id = None
            changed = True
        elif resource_type == "prompts" and tool.prompt_id == resource_id:
            tool.prompt_id = None
            changed = True
        elif resource_type == "knowledge-bases":
            config = dict(tool.impl_config or {})
            ids = [i for i in (config.get("knowledge_base_ids") or []) if i != resource_id]
            if len(ids) != len(config.get("knowledge_base_ids") or []):
                config["knowledge_base_ids"] = ids
                tool.impl_config = config
                changed = True
        if changed:
            unbound.append({"type": "Tool", "id": tool.id, "name": tool.name})

    await db.flush()
    return unbound


async def record_version(
    db: AsyncSession, resource_type: str, resource_id: int, snapshot: dict[str, Any]
) -> int:
    """登记一次资源版本，返回版本号。"""
    latest = (
        await db.execute(
            select(ResourceVersion)
            .where(ResourceVersion.resource_type == resource_type)
            .where(ResourceVersion.resource_id == resource_id)
            .order_by(ResourceVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    version = (latest.version + 1) if latest else 1
    db.add(
        ResourceVersion(
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            snapshot=snapshot,
        )
    )
    return version
