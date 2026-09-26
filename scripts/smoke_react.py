"""ReAct 回合冒烟：上传简历 → 发一条消息 → 观察思考/行动/观察 + 最终回答。

用法（后端需先启动）：
    python scripts/smoke_react.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="ReAct 回合冒烟")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--pdf", default="data/sample/resume_demo.pdf")
    parser.add_argument(
        "--content", default="帮我诊断这份简历，目标岗位是大数据开发实习生"
    )
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

        started = time.perf_counter()
        with client.stream(
            "POST",
            f"/sessions/{sid}/messages",
            json={
                "content": args.content,
                "attachment_id": attachment["id"],
                "agent_id": None,
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

    print("=" * 68)
    if failures:
        for item in failures:
            print(f"[FAIL] {item}")
        return 1
    print("通过：ReAct 循环、工具调用、步骤落库、详情懒加载都正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
