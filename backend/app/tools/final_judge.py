"""终裁官（有模型）：聚合技术面试官与 HR 的意见，给出判决与致命弱点。"""

from __future__ import annotations

from typing import Any

from app.tools._llm_runner import complete_with_prompt
from app.tools.base import ToolContext
from app.tools.registry import tool


@tool(
    impl_key="final_judge",
    title="终裁官",
    description=(
        "聚合技术面试官与 HR 的意见，输出一句话判决、致命弱点 Top3 与最该先做的事。"
        "拿到双方意见后再调用。"
    ),
    kind="llm",
    params_schema={
        "type": "object",
        "properties": {
            "resume": {"type": "string", "description": "简历正文"},
            "tech_findings": {"type": "string", "description": "技术面试官意见"},
            "hr_findings": {"type": "string", "description": "HR 面试官意见"},
        },
        "required": ["resume", "tech_findings", "hr_findings"],
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
    return await complete_with_prompt(ctx, role="终裁官")
