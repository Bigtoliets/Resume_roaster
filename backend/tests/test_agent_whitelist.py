"""Agent 工具白名单（D38）：页面勾选的就是白名单，且工具信息不再进 system prompt。"""

from __future__ import annotations

from typing import Any

from app.agent.loop import build_system_prompt, resolve_whitelist
from app.db.models import Agent, Prompt, Tool


def make_tool(**kwargs: Any) -> Tool:
    base: dict[str, Any] = {
        "id": 1,
        "name": "kb_search_perf",
        "display_name": "招聘语料检索",
        "description": "在 JD 语料库里检索",
        "impl_key": "kb_search",
        "is_enabled": True,
    }
    base.update(kwargs)
    return Tool(**base)


class FakeDB:
    """只回答 Prompt / Skill 的查询，够 build_system_prompt 用。"""

    def __init__(self, prompt: Prompt | None) -> None:
        self._prompt = prompt

    async def get(self, model: Any, _key: int) -> Any:
        if model is Prompt:
            return self._prompt
        return None


def test_whitelist_splits_runnable_and_blocked() -> None:
    tools = [
        make_tool(id=1, name="good", impl_key="kb_search"),
        make_tool(id=2, name="not_written", impl_key="not_written_yet"),
        make_tool(id=3, name="unset", impl_key=""),
    ]
    runnable, blocked = resolve_whitelist(tools)
    assert [tool.name for tool in runnable] == ["good"]
    assert [tool.name for tool in blocked] == ["not_written", "unset"]


def test_whitelist_keeps_unimplemented_out_of_the_model_view() -> None:
    """没实现的工具既不该出现在 tools 参数里，也不该出现在 prompt 里。"""
    runnable, blocked = resolve_whitelist([make_tool(impl_key="not_written_yet")])
    assert runnable == []
    assert len(blocked) == 1


async def test_system_prompt_has_no_tool_listing() -> None:
    agent = Agent(
        id=1,
        name="简历诊断官",
        persona="毒舌一点",
        prompt_id=1,
        skill_ids=[],
        max_rounds=12,
        tool_ids=[1, 2],
    )
    prompt = Prompt(
        id=1,
        name="主提示词",
        messages=[{"role": "system", "content": "你是简历诊断官。", "order": 1}],
    )
    text = await build_system_prompt(FakeDB(prompt), agent)  # type: ignore[arg-type]

    assert "你是简历诊断官。" in text
    assert "毒舌一点" in text
    assert "【可用工具】" not in text
    assert "招聘语料检索" not in text
    assert "kb_search_perf" not in text
