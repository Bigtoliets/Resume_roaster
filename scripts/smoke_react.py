"""ReAct 回合冒烟：上传简历 → 发一条消息 → 观察思考/行动/观察 + 最终回答。

用法（后端需先启动）：
    python scripts/smoke_react.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: 存档指针：`W2.T3`（第 2 窗口第 3 次工具调用）/ `W2.M5`（第 2 窗口第 5 条消息）
POINTER_RE = re.compile(r"^W(\d+)\.([MT])(\d+)$")


def verify_archive(pointers: list[str], session_id: int) -> list[str]:
    """把笔记里的每个指针拿去存档表里查一遍 —— 查得到才算"真能回查"。

    只查得到"格式对"是不够的：D48 有过一次真实翻车，指针格式全对，
    但 `messages.window_index/seq` 从来没被写过，`W1.M1` 是**死指针**。
    所以这里直接连库对表，而不是只做正则校验。

    ⚠️ 必须带 `session_id`：指针编号只在会话内唯一，不带会话条件时
    `.first()` 会撞上别的会话那一行，等于"验证通过但其实是错的"。
    """
    problems: list[str] = []
    if not pointers:
        print("  笔记里没有 refs，跳过指针回查")
        return problems

    root = Path(__file__).resolve().parent.parent
    if str(root / "backend") not in sys.path:
        sys.path.insert(0, str(root / "backend"))
    try:
        import asyncio

        from sqlalchemy import text

        from app.db.session import engine
    except Exception as exc:  # noqa: BLE001 - 连不上库就跳过，别让冒烟脚本本身崩
        print(f"  跳过指针回查（{type(exc).__name__}: {exc}）")
        return problems

    async def check() -> None:
        async with engine.connect() as conn:
            for pointer in pointers:
                match = POINTER_RE.match(pointer)
                if match is None:
                    print(f"  {pointer} → 格式非法")
                    problems.append(f"笔记 refs 指针格式非法：{pointer!r}")
                    continue
                window, kind, seq = int(match.group(1)), match.group(2), int(match.group(3))
                # kind 只可能是 M/T（正则已限死），表名不来自外部输入
                table = "messages" if kind == "M" else "tool_calls"
                row = (
                    await conn.execute(
                        text(
                            f"SELECT id FROM {table}"
                            " WHERE session_id=:sid AND window_index=:w AND seq=:s"
                        ),
                        {"sid": session_id, "w": window, "s": seq},
                    )
                ).first()
                if row is None:
                    print(f"  {pointer} → 存档里查不到（死指针）")
                    problems.append(f"笔记 refs 指针在存档里查不到：{pointer}")
                else:
                    print(f"  {pointer} → {table} #{row[0]}")

    asyncio.run(check())
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="ReAct 回合冒烟")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--pdf", default="data/sample/resume_demo.pdf")
    parser.add_argument(
        "--content", default="帮我诊断这份简历，目标岗位是大数据开发实习生"
    )
    parser.add_argument("--agent-id", type=int, default=None, help="指定 Agent；默认用第一个挂了工具的")
    args = parser.parse_args()

    api = f"{args.base_url.rstrip('/')}/api/v1"
    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"找不到简历：{pdf}（先跑 python scripts/make_demo_resume.py）")
        return 2

    failures: list[str] = []
    steps: list[dict] = []
    tool_call_ids: list[int] = []
    final_text = ""

    with httpx.Client(base_url=api, timeout=600) as client:
        session = client.post("/sessions", json={"title": "新会话"}).raise_for_status().json()
        sid = session["id"]
        print(f"会话 #{sid}")

        with pdf.open("rb") as handle:
            attachment = client.post(
                f"/sessions/{sid}/attachments",
                files={"file": (pdf.name, handle, "application/pdf")},
            ).raise_for_status().json()
        print(f"附件 #{attachment['id']}：{attachment['filename']}")

        agent_rows = client.get("/agents").raise_for_status().json()
        agent = (
            next((a for a in agent_rows if a["id"] == args.agent_id), None)
            if args.agent_id
            else next((a for a in agent_rows if a.get("tool_ids")), None)
        )
        if agent is not None:
            print(
                f"Agent #{agent['id']} {agent['name']}｜换页阈值 "
                f"{agent['context_threshold_pct']}%｜工具 {len(agent.get('tool_ids') or [])} 个"
            )

        started = time.perf_counter()
        with client.stream(
            "POST",
            f"/sessions/{sid}/messages",
            json={
                "content": args.content,
                "attachment_id": attachment["id"],
                "agent_id": agent["id"] if agent else None,
            },
        ) as stream:
            if stream.status_code >= 400:
                print("请求失败：", stream.read().decode("utf-8", "replace")[:400])
                return 1
            event = ""
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if event == "stage":
                    print(f"  [stage] {data.get('stage')} · agent={data.get('agent')}")
                elif event == "step":
                    kind = data.get("type")
                    if kind == "action":
                        args_text = json.dumps(data.get("arguments"), ensure_ascii=False)[:90]
                        print(f"  [行动] {data.get('tool_display')} ← {args_text}")
                    elif kind == "observation":
                        print(f"  [观察] {data.get('summary', '')[:100]}")
                        if data.get("tool_call_id"):
                            tool_call_ids.append(int(data["tool_call_id"]))
                    elif kind == "thought":
                        print(f"  [思考] {str(data.get('text'))[:90]}")
                    steps.append(data)
                elif event == "assistant":
                    final_text = str(data.get("content") or "")
                    steps = data.get("steps") or steps
                elif event == "approval_required":
                    print(f"  [需确认] {data.get('tool_display')} → 自动批准")
                    client.post(f"/tool-calls/{data['tool_call_id']}/approve", json={"approved": True})
                elif event == "error":
                    failures.append(f"{data.get('code')}: {data.get('msg')}")
                    print(f"  [错误] {data.get('code')}: {data.get('msg')}")
                elif event == "done":
                    print(f"  [完成] {data.get('elapsed_ms')}ms")

        elapsed = time.perf_counter() - started
        print("\n" + "=" * 68)
        print(f"步骤数：{len(steps)}｜工具调用：{len(tool_call_ids)}｜耗时 {elapsed:.1f}s")
        if not final_text:
            failures.append("没有拿到最终回答")
        else:
            print(f"最终回答（{len(final_text)} 字）：{final_text[:300].replace(chr(10), ' ')}…")

        # 懒加载验证：展开一次工具调用详情
        if tool_call_ids:
            detail = client.get(f"/tool-calls/{tool_call_ids[0]}").json()
            print(
                f"工具调用 #{detail['id']} 详情：{detail['tool_name']}｜"
                f"status={detail['status']}｜结果大小 {len(json.dumps(detail['result'], ensure_ascii=False))} 字符"
            )
        else:
            failures.append("模型一次工具都没调用（ReAct 没跑起来）")

        messages = client.get(f"/sessions/{sid}/messages").json()
        assistant_messages = [m for m in messages if m["role"] == "assistant"]
        if not assistant_messages:
            failures.append("助手消息没有落库")

        # ---- D48/D49：存档坐标（笔记里的 W*.M* 靠它回查）----
        print("-" * 68)
        print("存档坐标：")
        for row in messages:
            content = row.get("content") or {}
            label = str(content.get("text") or content.get("filename") or "")[:32]
            label = label.replace("\n", " ")
            print(
                f"  #{row['id']:>4} {row['role']:>9} {row['type']:>6} "
                f"W{row.get('window_index')}.M{row.get('seq')}  {label}"
            )
        text_rows = [m for m in messages if m["type"] == "text"]
        missing = [m["id"] for m in text_rows if not m.get("seq")]
        if missing:
            failures.append(f"这些纯文本消息没有存档坐标：{missing}（笔记里的 W*.M* 会查不到行）")
        coords = [(m["window_index"], m["seq"]) for m in messages if m.get("seq")]
        if len(coords) != len(set(coords)):
            failures.append("存档坐标有重复，recall 会取错行")

        # ---- D48/D49：任务笔记 ----
        notes = client.get(f"/sessions/{sid}/notes").json()
        print(f"任务笔记：{len(notes)} 条（0 条 = 上下文没到换页阈值，属正常）")
        for row in notes:
            body = row["content"]
            print(f"  笔记 #{row['id']} run={row['run_id']} 窗口 W{row['window_index']}")
            # progress 是结构化三段（stage / done / missing）；旧笔记存的是字符串，
            # 这里统一成对象再打印，两种格式都能读。
            progress = body.get("progress") or {}
            if isinstance(progress, str):
                progress = {"stage": progress}
            print(f"    goal     : {body.get('goal')}")
            print(f"    stage    : {progress.get('stage')}")
            print(f"    done     : {json.dumps(progress.get('done'), ensure_ascii=False)}")
            print(f"    missing  : {json.dumps(progress.get('missing'), ensure_ascii=False)}")
            print(f"    plan     : {json.dumps(body.get('plan'), ensure_ascii=False)}")
            print(f"    refs     : {json.dumps(body.get('refs'), ensure_ascii=False)}")
        pointers = [
            str(ref.get("pointer") or "")
            for row in notes
            for ref in (row.get("content") or {}).get("refs") or []
        ]
        if notes:
            print("指针回查：")
            failures.extend(verify_archive(pointers, sid))

    print("=" * 68)
    if failures:
        for item in failures:
            print(f"[FAIL] {item}")
        return 1
    print("通过：ReAct 循环、工具调用、步骤落库、详情懒加载都正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
