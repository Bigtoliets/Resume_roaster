"""HR 面试官（有模型）：查硬性门槛、个人贡献与量化产出。"""

from __future__ import annotations

from typing import Any

from app.tools._llm_runner import complete_with_prompt
from app.tools.base import ToolContext
from app.tools.registry import tool


@tool(
    impl_key="interview_hr",
    title="HR 面试官",
    description="让大厂 HRBP 检查硬性门槛、个人贡献与量化产出、表达逻辑。",
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
    return await complete_with_prompt(ctx, role="HR 面试官")
