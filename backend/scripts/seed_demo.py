"""写入演示配置（D31 ReAct 模型）：

    一个 Agent（主循环）+ 一组工具（知识库检索 / 面试官 / 终裁 / PDF 解析）
    Prompt、Skill、知识库都是独立资源，只在工具或 Agent 上被引用。

用法（backend 目录下）：
    python -m scripts.seed_demo
"""

from __future__ import annotations

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from app.agent.prompts import DEFAULT_AGENT_SYSTEM, TOOL_PROMPT_HINTS
from app.core.config import settings
from app.core.security import encrypt
from app.db.base import utcnow
from app.db.models import Agent, KnowledgeBase, ModelConfig, Prompt, Skill, Tool
from app.db.session import SessionLocal
from app.services.refs import record_version, snapshot_agent

#: 旧架构（9 节点 + 场景编排）留下的 Agent，新模型下已无意义，seed 时软删
LEGACY_AGENT_NAMES = [
    "agent_resume_parser",
    "agent_tech_lead",
    "agent_hrbp",
    "agent_supervisor",
    "agent_advisor",
    "agent_roadmap_coach",
    "agent_router",
    "agent_chat_assistant",
]


async def main() -> None:
    async with SessionLocal() as db:
        chat = await upsert(
            db,
            ModelConfig,
            name="deepseek-chat",
            defaults={
                "description": "主循环与面试官工具：便宜、快、支持函数调用",
                "base_url": settings.deepseek_base_url,
                "api_key_cipher": encrypt(settings.deepseek_api_key),
                "model_name": settings.default_chat_model,
                "default_params": {"temperature": 0.6, "max_tokens": 4096},
                "timeout_s": 120,
                "is_default": True,
            },
        )
        reasoner = await upsert(
            db,
            ModelConfig,
            name="deepseek-reasoner",
            defaults={
                "description": "终裁类工具用（不支持函数调用，只做单次生成）",
                "base_url": settings.deepseek_base_url,
                "api_key_cipher": encrypt(settings.deepseek_api_key),
                "model_name": settings.default_reasoner_model,
                "default_params": {"max_tokens": 8192},
                "timeout_s": 240,
                "is_default": False,
            },
        )

        prompt_main = await upsert(
            db,
            Prompt,
            name="prompt_agent_main",
            defaults={
                "description": "Agent 主循环的 ReAct 系统提示词",
                "messages": [{"role": "system", "content": DEFAULT_AGENT_SYSTEM, "order": 1}],
                "params": {},
                "variables": [],
            },
        )
        prompt_tech = await upsert(
            db,
            Prompt,
            name="prompt_tool_tech_interviewer",
            defaults={
                "description": "技术面试官工具：只挑技术深度",
                "messages": [
                    {
                        "role": "system",
                        "content": TOOL_PROMPT_HINTS["tech_interviewer"]
                        + "\n输出用 Markdown 列表，每条包含：问题 / 简历原句 / JD 依据 / 严重度(1-5)。",
                        "order": 1,
                    },
                    {
                        "role": "user",
                        "content": "目标岗位要求：\n{{job_requirements}}\n\n候选人简历：\n{{resume}}",
                        "order": 2,
                    },
                ],
                "params": {"temperature": 0.7},
                "variables": ["job_requirements", "resume"],
            },
        )
        prompt_hr = await upsert(
            db,
            Prompt,
            name="prompt_tool_hr_interviewer",
            defaults={
                "description": "HR 面试官工具：硬性门槛 + 产出 + 表达",
                "messages": [
                    {
                        "role": "system",
                        "content": TOOL_PROMPT_HINTS["hr_interviewer"]
                        + "\n输出用 Markdown 列表，每条包含：问题 / 简历原句 / 判断依据。",
                        "order": 1,
                    },
                    {
                        "role": "user",
                        "content": "目标岗位要求：\n{{job_requirements}}\n\n候选人简历：\n{{resume}}",
                        "order": 2,
                    },
                ],
                "params": {"temperature": 0.5},
                "variables": ["job_requirements", "resume"],
            },
        )
        prompt_judge = await upsert(
            db,
            Prompt,
            name="prompt_tool_final_judge",
            defaults={
                "description": "终裁官工具：聚合已有结论，排出致命弱点",
                "messages": [
                    {
                        "role": "system",
                        "content": TOOL_PROMPT_HINTS["final_judge"]
                        + "\n输出结构：① 一句话判决 ② 致命弱点 Top3（含简历原句） ③ 最该先做的一件事。",
                        "order": 1,
                    },
                    {
                        "role": "user",
                        "content": (
                            "简历：\n{{resume}}\n\n技术面试官意见：\n{{tech_findings}}\n\n"
                            "HR 面试官意见：\n{{hr_findings}}"
                        ),
                        "order": 2,
                    },
                ],
                "params": {},
                "variables": ["resume", "tech_findings", "hr_findings"],
            },
        )

        kb_jd = await upsert(
            db,
            KnowledgeBase,
            name="JD 语料库",
            defaults={
                "description": "企业招聘信息，一行 = 一篇 JD",
                "collection": "jd",
                "embedding_model": settings.embedding_model,
                "top_k": 10,
                "chunk_config": {},
                "filter_fields": {"id_prefix": ""},
                "is_enabled": True,
            },
        )
        skill_cache = await upsert(
            db,
            Skill,
            name="缓存方案拷问",
            defaults={
                "description": "提到缓存就必须追问四个场景",
                "instructions": (
                    "涉及 Redis / 缓存时，必须确认四件事：穿透（布隆过滤器/空值缓存）、"
                    "雪崩（过期时间打散/多级缓存）、击穿（互斥锁/逻辑过期）、"
                    "与数据库一致性（延迟双删/订阅 binlog）。缺失即视为「只有名词没有场景」。"
                ),
                "is_enabled": True,
            },
        )
        skill_metric = await upsert(
            db,
            Skill,
            name="量化产出审查",
            defaults={
                "description": "检查是否有可验证的业务指标与个人贡献",
                "instructions": (
                    "每个项目都要能回答：QPS/延迟/数据量/成本是多少？个人贡献与团队成果如何区分？"
                    "优化前后对比是多少？缺失就问，不要替候选人假设。"
                ),
                "is_enabled": True,
            },
        )

        tool_kb = await upsert(
            db,
            Tool,
            name="kb_search_perf",
            defaults={
                "display_name": "招聘语料检索",
                "description": (
                    "在真实 JD 与面经语料库里做混合检索。需要了解目标岗位的真实要求、"
                    "或想看同类岗位的面试考点时调用它。"
                ),
                "impl_key": "kb_search",
                "impl_config": {
                    # 一期只维护 jd 一个 collection（面经表已下线，D33）
                    "knowledge_base_ids": [kb_jd.id],
                    "id_prefix": "",
                },
                "requires_approval": False,
                "timeout_s": 60,
                "retry_times": 1,
                "is_enabled": True,
            },
        )
        tool_pdf = await upsert(
            db,
            Tool,
            name="resume_pdf_extract",
            defaults={
                "display_name": "简历 PDF 读取",
                "description": "读取附件里的简历正文。用户上传 PDF 后，先调用它拿到文本再分析。",
                "impl_key": "pdf_extract",
                "impl_config": {},
                "requires_approval": False,
                "timeout_s": 60,
                "retry_times": 1,
                "is_enabled": True,
            },
        )
        tool_tech = await upsert(
            db,
            Tool,
            name="interview_tech_lead",
            defaults={
                "display_name": "技术面试官",
                "description": (
                    "让大厂技术面试官针对简历与岗位要求挑技术深度问题。"
                    "需要技术侧质疑时调用，返回值是带简历原句的质疑列表。"
                ),
                "impl_key": "interview_tech_lead",
                "impl_config": {},
                "prompt_id": prompt_tech.id,
                "model_config_id": chat.id,
                "requires_approval": False,
                "timeout_s": 180,
                "retry_times": 1,
                "is_enabled": True,
            },
        )
        tool_hr = await upsert(
            db,
            Tool,
            name="interview_hr",
            defaults={
                "display_name": "HR 面试官",
                "description": "让大厂 HRBP 检查硬性门槛、个人贡献与量化产出、表达逻辑。",
                "impl_key": "interview_hr",
                "impl_config": {},
                "prompt_id": prompt_hr.id,
                "model_config_id": chat.id,
                "requires_approval": False,
                "timeout_s": 180,
                "retry_times": 1,
                "is_enabled": True,
            },
        )
        tool_judge = await upsert(
            db,
            Tool,
            name="final_judge",
            defaults={
                "display_name": "终裁官",
                "description": (
                    "聚合技术面试官与 HR 的意见，输出一句话判决、致命弱点 Top3 与最该先做的事。"
                    "拿到双方意见后再调用。"
                ),
                "impl_key": "final_judge",
                "impl_config": {},
                "prompt_id": prompt_judge.id,
                "model_config_id": reasoner.id,
                "requires_approval": False,
                "timeout_s": 240,
                "retry_times": 1,
                "is_enabled": True,
            },
        )

        agent = await upsert(
            db,
            Agent,
            name="简历诊断官",
            defaults={
                "description": "ReAct 主循环：读简历 → 查真实语料 → 请两位面试官质疑 → 终裁并给建议",
                "agent_type": "diagnosis",
                "model_config_id": chat.id,
                "prompt_id": prompt_main.id,
                "persona": "",
                "skill_ids": [skill_cache.id, skill_metric.id],
                "tool_ids": [
                    tool_pdf.id,
                    tool_kb.id,
                    tool_tech.id,
                    tool_hr.id,
                    tool_judge.id,
                ],
                "max_rounds": 12,
                "parallel_tools": True,
                "params_override": {},
            },
        )

        await record_version(db, "agents", agent.id, await snapshot_agent(db, agent))

        # 清理旧架构的 Agent（软删，历史会话仍可读）
        retired = 0
        for name in LEGACY_AGENT_NAMES:
            row = (await db.execute(select(Agent).where(Agent.name == name))).scalar_one_or_none()
            if row is not None and row.deleted_at is None:
                row.deleted_at = utcnow()
                row.is_enabled = False
                retired += 1
        await db.commit()
        tools = "、".join(t.display_name or t.name for t in (tool_pdf, tool_kb, tool_tech, tool_hr, tool_judge))
        print("演示配置写入完成：1 个 Agent、4 个 Prompt、1 个知识库、2 个 Skill、5 个工具。")
        print(f"Agent「{agent.name}」可用工具：{tools}")
        if retired:
            print(f"已软删 {retired} 个旧架构 Agent")


async def upsert(db, model, *, name: str, defaults: dict):  # type: ignore[no-untyped-def]
    row = (await db.execute(select(model).where(model.name == name))).scalar_one_or_none()
    if row is None:
        row = model(name=name, **defaults)
        db.add(row)
    else:
        for key, value in defaults.items():
            setattr(row, key, value)
        row.deleted_at = None
        row.is_enabled = True
    await db.flush()
    return row


if __name__ == "__main__":
    asyncio.run(main())
