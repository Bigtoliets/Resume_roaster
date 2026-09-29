"""六类平台资源的统一 CRUD（由注册表生成，避免六份重复代码）。

注意一个容易踩的坑：**FastAPI 在注册路由时就解析函数签名**，因此入参注解不能是
"运行时才拿到的变量"（例如 `payload: spec.schema_in`）。那样会被当成 query 参数，
表现就是创建/修改永远返回 422 且提示 `missing query payload`。
所以这里先定义函数、显式写回 `__annotations__`，再用 `add_api_route` 注册。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.cache import (
    cached,
    invalidate_prefix,
    invalidate_resource,
    resource_key,
    resource_list_key,
)
from app.core.security import decrypt, encrypt, mask
from app.db.models import Agent, KnowledgeBase, ModelConfig, Prompt, Skill, Tool
from app.schemas.resource import (
    AgentIn,
    AgentOut,
    KnowledgeBaseIn,
    KnowledgeBaseOut,
    ModelConfigIn,
    ModelConfigOut,
    PromptIn,
    PromptOut,
    SkillIn,
    SkillOut,
    ToolIn,
    ToolOut,
)
from app.services.refs import (
    find_referencing_agents,
    record_version,
    snapshot_agent,
    unbind_references,
)
from app.skill.store import SkillFormatError, create_skill
from app.skill.sync import is_orphan
from app.tools.registry import get_impl, impl_status


@dataclass(frozen=True)
class ResourceSpec:
    slug: str
    label: str
    model: type
    schema_in: type[BaseModel]
    schema_out: type[BaseModel]


RESOURCE_SPECS: list[ResourceSpec] = [
    ResourceSpec("models", "模型配置", ModelConfig, ModelConfigIn, ModelConfigOut),
    ResourceSpec("prompts", "Prompts", Prompt, PromptIn, PromptOut),
    ResourceSpec("tools", "Tools", Tool, ToolIn, ToolOut),
    ResourceSpec("knowledge-bases", "知识库", KnowledgeBase, KnowledgeBaseIn, KnowledgeBaseOut),
    ResourceSpec("skills", "Skills", Skill, SkillIn, SkillOut),
    ResourceSpec("agents", "Agents", Agent, AgentIn, AgentOut),
]

SPEC_BY_SLUG = {spec.slug: spec for spec in RESOURCE_SPECS}


def _to_out(spec: ResourceSpec, obj: Any) -> BaseModel:
    """ORM -> 出参。模型配置额外处理密钥：只回掩码，绝不回明文。"""
    if spec.slug == "models":
        return ModelConfigOut(
            id=obj.id,
            name=obj.name,
            description=obj.description,
            base_url=obj.base_url,
            api_key_masked=mask(decrypt(obj.api_key_cipher)),
            model_name=obj.model_name,
            default_params=obj.default_params or {},
            timeout_s=obj.timeout_s,
             # D48 新增列：出参 schema 里是必填，不显式传就会 ValidationError
             # （表现为整个 GET /models 500，模型页直接打不开）
             context_window=obj.context_window or 65536,
            is_default=obj.is_default,
            is_enabled=obj.is_enabled,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )
    if spec.slug == "tools":
        out = ToolOut.model_validate(obj)
        impl = get_impl(obj.impl_key)
        out.impl_status = impl_status(obj)
        if impl is not None:
            out.impl_title = impl.title
            out.impl_kind = impl.kind
            out.needs_prompt = impl.needs_prompt
        return out
    if spec.slug == "skills":
        # 孤儿动态判定：表里有行、磁盘上没有目录（SD22）
        out = SkillOut.model_validate(obj)
        out.is_orphan = is_orphan(obj.name)
        return out
    return spec.schema_out.model_validate(obj)


def _apply_payload(spec: ResourceSpec, obj: Any, payload: BaseModel) -> Any:
    data = payload.model_dump(exclude_unset=True)
    if spec.slug == "models":
        api_key = data.pop("api_key", None)
        if api_key:
            obj.api_key_cipher = encrypt(api_key)
    for key, value in data.items():
        setattr(obj, key, value)
    return obj


async def _get_or_404(db: AsyncSession, spec: ResourceSpec, item_id: int) -> Any:
    obj = await db.get(spec.model, item_id)
    if obj is None or getattr(obj, "deleted_at", None) is not None:
        raise HTTPException(status_code=404, detail=f"{spec.label} #{item_id} 不存在")
    return obj


def build_resource_router(spec: ResourceSpec) -> APIRouter:
    router = APIRouter(prefix=f"/{spec.slug}", tags=[spec.label])

    # ---------------- 列表 ----------------
    async def list_items(
        db: AsyncSession = Depends(get_db),
        q: str | None = Query(default=None, description="按名称模糊搜索"),
        limit: int = Query(default=100, le=500),
        offset: int = 0,
    ) -> list[Any]:
        key = f"{resource_list_key(spec.slug)}:q={q or ''}:l={limit}:o={offset}"

        async def load() -> list[Any]:
            stmt = select(spec.model).where(spec.model.deleted_at.is_(None))
            if q:
                stmt = stmt.where(spec.model.name.like(f"%{q}%"))
            stmt = stmt.order_by(spec.model.id.desc()).limit(limit).offset(offset)
            rows = (await db.execute(stmt)).scalars().all()
            return [_to_out(spec, row).model_dump(mode="json") for row in rows]

        return await cached(key, load)

    router.add_api_route(
        "", list_items, methods=["GET"], response_model=list[spec.schema_out], summary=f"列出{spec.label}"
    )

    # ---------------- 创建 ----------------
    async def create_item(payload: Any = Body(...), db: AsyncSession = Depends(get_db)) -> Any:
        existing = (
            await db.execute(
                select(spec.model)
                .where(spec.model.name == payload.name)
                .where(spec.model.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"{spec.label} 名称已存在：{payload.name}")
        if spec.slug == "skills":
            # SD22：新建 = 真的在 backend/skill/<name>/ 落一份 SKILL.md。
            # 目录已存在（或名字不合规范）都拒绝——文件是唯一真源，不能只写库。
            try:
                create_skill(payload.name, payload.description)
            except SkillFormatError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        obj = _apply_payload(spec, spec.model(), payload)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        invalidate_resource(spec.slug, obj.id)
        return _to_out(spec, obj)

    # 关键：把运行时变量写进签名，FastAPI 才会把它当请求体
    create_item.__annotations__["payload"] = spec.schema_in
    router.add_api_route(
        "",
        create_item,
        methods=["POST"],
        response_model=spec.schema_out,
        status_code=201,
        summary=f"创建{spec.label}",
    )

    # ---------------- 详情 ----------------
    async def get_item(item_id: int, db: AsyncSession = Depends(get_db)) -> Any:
        async def load() -> dict[str, Any]:
            row = await _get_or_404(db, spec, item_id)
            return _to_out(spec, row).model_dump(mode="json")

        return await cached(resource_key(spec.slug, item_id), load)

    router.add_api_route(
        "/{item_id}", get_item, methods=["GET"], response_model=spec.schema_out, summary=f"获取{spec.label}"
    )

    # ---------------- 修改 ----------------
    async def update_item(
        item_id: int, payload: Any = Body(...), db: AsyncSession = Depends(get_db)
    ) -> Any:
        obj = await _get_or_404(db, spec, item_id)
        if spec.slug == "skills":
            # SD22：不可编辑。正文在文件里，表只是索引——页面改的值会被启动对账覆盖，
            # 与其静默回滚，不如直接拒绝，让人去改文件。
            raise HTTPException(
                status_code=405,
                detail=(
                    "Skill 不可从页面编辑：正文在 backend/skill/"
                    f"{obj.name}/SKILL.md 里，请直接改文件"
                ),
            )
        _apply_payload(spec, obj, payload)
        await db.commit()
        await db.refresh(obj)

        if spec.slug == "agents":
            # Agent 保存即打版本快照：会话创建时固化，保证历史可复现
            await record_version(db, "agents", obj.id, await snapshot_agent(db, obj))
            await db.commit()
        invalidate_resource(spec.slug, obj.id)
        return _to_out(spec, obj)

    update_item.__annotations__["payload"] = spec.schema_in
    router.add_api_route(
        "/{item_id}",
        update_item,
        methods=["PUT"],
        response_model=spec.schema_out,
        summary=f"修改{spec.label}",
    )

    # ---------------- 删除（软删 + 引用保护 / cascade 解绑） ----------------
    async def delete_item(
        item_id: int,
        cascade: bool = Query(
            default=False,
            description="被引用时是否自动解绑后删除（默认 false：返回 409 拒绝）",
        ),
        db: AsyncSession = Depends(get_db),
    ) -> dict[str, Any]:
        from app.db.base import utcnow

        obj = await _get_or_404(db, spec, item_id)
        if spec.slug == "skills" and not is_orphan(obj.name):
            # SD22：页面只允许删"孤儿行"（磁盘上已经没有的）。删目录不可逆，
            # 而那个目录里可能正躺着蒸馏线路积累的经验。
            raise HTTPException(
                status_code=409,
                detail=(
                    f"磁盘上还存在 backend/skill/{obj.name}/，不能从页面删除。"
                    "要清理请手动删目录（删完这里会变成孤儿行，那时可以删）。"
                ),
            )
        unbound: list[dict[str, Any]] = []
        if spec.slug != "agents":
            refs = await find_referencing_agents(db, spec.slug, item_id)
            if refs and not cascade:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            f"该{spec.label}仍被 {len(refs)} 处引用。"
                            "可以在这条配置上选择「解绑并删除」，或先手动解绑"
                        ),
                        "referenced_by": refs,
                    },
                )
            if refs and cascade:
                unbound = await unbind_references(db, spec.slug, item_id)
        obj.deleted_at = utcnow()
        await db.commit()
        invalidate_resource(spec.slug, item_id)
        # 解绑改动了 Agent 与工具的引用关系，相关缓存一并清掉
        if unbound:
            invalidate_prefix("resources:agents")
            invalidate_prefix("resources:tools")
        return {"deleted": item_id, "unbound": unbound}

    router.add_api_route(
        "/{item_id}", delete_item, methods=["DELETE"], status_code=200, summary=f"删除{spec.label}"
    )

    return router


def build_all_resource_routers() -> list[APIRouter]:
    return [build_resource_router(spec) for spec in RESOURCE_SPECS]
