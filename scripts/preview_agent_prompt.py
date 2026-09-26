"""打印某个 Agent 最终装配出来的 system prompt（含 {{tools}} 注入结果）。

用法：
    python scripts/preview_agent_prompt.py            # 默认取第一个 Agent
    python scripts/preview_agent_prompt.py 36         # 指定 Agent ID

用来确认：提示词里的 {{tools}} / {{max_rounds}} 是否真的被替换、
工具清单是否只包含该 Agent 勾选的工具。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main(agent_id: int | None) -> int:
    from sqlalchemy import select

    from app.agent.loop import build_system_prompt
    from app.db.models import Agent
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        if agent_id is None:
            agent = (await db.execute(select(Agent).limit(1))).scalar_one_or_none()
        else:
            agent = await db.get(Agent, agent_id)
        if agent is None:
            print("没有找到 Agent")
            return 1

        print(f"=== Agent #{agent.id} {agent.name}（tool_ids={agent.tool_ids}）===")
        print(f"prompt_id={agent.prompt_id}  max_rounds={agent.max_rounds}")
        print("=" * 70)
        text = await build_system_prompt(db, agent)
        print(text)
        print("=" * 70)

        leftovers = [token for token in ("{{tools}}", "{{max_rounds}}") if token in text]
        if leftovers:
            print(f"✗ 仍有未替换的变量：{leftovers}")
            return 1
        print("✓ 变量已全部替换")
        return 0


if __name__ == "__main__":
    arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    sys.exit(asyncio.run(main(arg)))
