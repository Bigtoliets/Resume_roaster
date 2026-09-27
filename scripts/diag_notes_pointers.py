"""诊断「笔记里的指针能不能查到数据」。

用法：python scripts/diag_notes_pointers.py [条数]

做三件事：
  1. 打印最近的 RunNote 及其 refs 指针；
  2. 对每个指针按 (run_id, window_index, seq) 回查 messages / tool_calls，报告命中什么；
  3. 列出该 run 下所有带坐标的记录，方便和 refs 对照，看是编号错位还是根本没落库。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main(limit: int) -> int:
    from sqlalchemy import select

    from app.agent.notes import POINTER_RE
    from app.db.models import Message, RunNote, ToolCall
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        notes = (
            await db.execute(select(RunNote).order_by(RunNote.id.desc()).limit(limit))
        ).scalars().all()
        if not notes:
            print("没有 RunNote 记录（说明还没发生过换页）")
            return 0

        for note in notes:
            content = note.content or {}
            print(f"=== RunNote #{note.id}  run_id={note.run_id}  window_index={note.window_index} ===")
            print(f"    goal: {str(content.get('goal'))[:60]}")
            print(f"    progress: {(content.get('progress') or {}).get('stage')}")

            refs = content.get("refs") or []
            if not refs:
                print("    refs: （空）")
            for ref in refs:
                pointer = str(ref.get("pointer") or "")
                match = POINTER_RE.match(pointer)
                if match is None:
                    print(f"    {pointer or '(空)':10} → 指针格式非法")
                    continue
                window, kind, seq = int(match.group(1)), match.group(2), int(match.group(3))
                model = Message if kind == "M" else ToolCall
                row = (
                    await db.execute(
                        select(model).where(
                            model.run_id == note.run_id,
                            model.window_index == window,
                            model.seq == seq,
                        )
                    )
                ).scalars().first()
                found = "查不到！" if row is None else f"命中 {getattr(row, 'tool_name', None) or getattr(row, 'role', '')}"
                print(f"    {pointer:10} kind={str(ref.get('kind')):8} → {found}")
                print(f"               期望: {str(ref.get('summary'))[:56]}")

            calls = (
                await db.execute(
                    select(ToolCall)
                    .where(ToolCall.run_id == note.run_id)
                    .order_by(ToolCall.window_index, ToolCall.seq)
                )
            ).scalars().all()
            print("    该 run 的工具调用坐标：")
            for call in calls:
                print(
                    f"      W{call.window_index}.T{call.seq}  {call.tool_name:22}"
                    f" round={call.round_index} status={call.status}"
                )
            print()
    return 0


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    sys.exit(asyncio.run(main(count)))
