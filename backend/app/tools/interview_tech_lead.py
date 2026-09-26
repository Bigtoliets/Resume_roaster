"""技术面试官（有模型）：按页面选的 Prompt 挑技术深度问题。"""

from __future__ import annotations

from typing import Any

from app.tools._llm_runner import complete_with_prompt
from app.tools.base import ToolContext
from app.tools.registry import tool


@tool(
    impl_key="interview_tech_lead",
    title="技术面试官",
    description="让大厂技术面试官针对简历与岗位要求挑技术深度问题，质疑必须引用简历原句。",
    kind="llm",
    params_schema={
        "type": "object",
        "properties": {
            "resume": {"type": "string", "description": "简历正文"},
            "job_requirements": {"type": "string", "description": "目标岗位要求（来自检索结果）"},
        },
        "required": ["resume", "job_requirements"],
    },
    returns_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "summary": {"type": "string"},
            "data": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    },
)
async def run(input: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return await complete_with_prompt(ctx, role="技术面试官")
