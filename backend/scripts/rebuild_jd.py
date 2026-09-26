"""按岗位边界重建 jd 语料（D34）。

背景：原导入是按固定字数切的，一条岗位的「薪资/经验/技术标签/岗位职责」被切散到
不同行里，向量成了"多个不相关岗位的均值"，语义检索接近随机。

本脚本把 47 行按 id 顺序拼回整篇 markdown，按 `【岗位 N】` 重新切分——一条岗位一行，
再把方向标题并进岗位正文作为上下文，最后重算 dense 写回。

用法（backend 目录下）：
    python -m scripts.rebuild_jd --dry-run   # 只看切分结果，不动数据
    python -m scripts.rebuild_jd             # 真的重建
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKUP = Path("D:/develop/python_project/Resume_roaster/preview/_jd_merged.md")

JOB_RE = re.compile(r"【岗位\s*(\d+)】")
#: 原文里的 markdown 标题被转义成了 `\##`，且方向标题与首个岗位在同一行，
#: 所以要用「（共 N 条）」或「【岗位」作为终止标记，避免把岗位正文一起吃进来。
DIRECTION_RE = re.compile(
    r"^\\{0,2}#{1,3}\s*方向[一二三四五六七八九十\d]*\s*[:：]\s*"
    r"(.+?)\s*(?:（共\s*\d+\s*条）|【岗位|$)",
    re.M,
)


def _sort_key(doc_id: str) -> tuple[int, str]:
    """id 形如 <前缀>_12：必须按数字排序，字符串排序会把 _10 排到 _1 后面。"""
    tail = doc_id.rsplit("_", 1)[-1]
    return (int(tail), doc_id) if tail.isdigit() else (10**9, doc_id)


def merge(rows: list[dict]) -> str:
    ordered = sorted(rows, key=lambda row: _sort_key(str(row["id"])))
    return "\n".join(str(row["content"]) for row in ordered)


def split_jobs(markdown: str) -> list[dict[str, str]]:
    """按 `【岗位 N】` 切分；方向标题并进该方向下每个岗位的正文。

    语料里同一个岗位号会出现两次：一次是残片（上一段的尾巴 + 标题 + 半截正文），
    一次是完整版。**同号保留 content 最长的那条**——去重后正好 100 条。
    """
    directions = [(m.start(), m.group(1).strip()) for m in DIRECTION_RE.finditer(markdown)]
    marks = [(m.start(), m.group(1)) for m in JOB_RE.finditer(markdown)]
    if not marks:
        raise SystemExit("没找到任何 `【岗位 N】` 标记，切分规则与语料不匹配")

    merged: dict[str, dict[str, str]] = {}
    for index, (start, number) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(markdown)
        body = markdown[start:end].strip()
        body = re.sub(r"\n{3,}", "\n\n", body)
        direction = next((name for pos, name in reversed(directions) if pos < start), "")
        title_match = re.match(r"【岗位\s*\d+】\s*(.*)", body.split("\n", 1)[0])
        title = (title_match.group(1).strip() if title_match else "")[:80]
        content = f"【方向】{direction}\n{body}" if direction else body
        job = {
            "id": f"jd_{int(number):03d}",
            "content": content,
            "title": title,
            "direction": direction,
        }
        current = merged.get(job["id"])
        if current is None or len(job["content"]) > len(current["content"]):
            merged[job["id"]] = job
    return [merged[key] for key in sorted(merged)]


async def main_async(dry_run: bool) -> int:
    from app.vector.milvus_client import TEXT_FIELD, count_documents, get_client

    client = get_client()
    rows = client.query(collection_name="jd", filter="", output_fields=[TEXT_FIELD], limit=1000)
    print(f"线上现有 {len(rows)} 行（count(*) = {count_documents('jd')}）")

    markdown = merge(rows)
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    BACKUP.write_text(markdown, encoding="utf-8")
    print(f"拼回整篇 markdown：{len(markdown)} 字 -> 备份到 {BACKUP}")

    jobs = split_jobs(markdown)
    print(f"\n按 `【岗位 N】` 切出 {len(jobs)} 条岗位")
    lengths = [len(job["content"]) for job in jobs]
    print(f"  每条平均 {sum(lengths) // len(lengths)} 字｜最短 {min(lengths)}｜最长 {max(lengths)}")
    by_direction: dict[str, int] = {}
    for job in jobs:
        by_direction[job["direction"] or "（无方向）"] = by_direction.get(job["direction"] or "（无方向）", 0) + 1
    for name, count in by_direction.items():
        print(f"  {name}: {count} 条")
    print("\n前 3 条预览：")
    for job in jobs[:3]:
        head = job["content"].replace("\n", " ")[:110]
        print(f"  {job['id']}  {head}…")

    if dry_run:
        print("\ndry-run：未改动 Milvus。")
        return 0

    from app.vector.embedding import get_embedding_service

    service = get_embedding_service()
    texts = [job["content"] for job in jobs]
    print(f"\n重算 dense（{len(texts)} 条，provider={service.resolve_provider()}）…")
    dense_list = await service.embed_dense(texts)

    old_ids = [str(row["id"]) for row in rows]
    quoted = ", ".join(f'"{i}"' for i in old_ids)
    client.delete(collection_name="jd", filter=f"id in [{quoted}]")
    print(f"已删除旧的 {len(old_ids)} 行")

    payload = [
        {"id": job["id"], "content": job["content"], "dense": dense}
        for job, dense in zip(jobs, dense_list, strict=True)
    ]
    for start in range(0, len(payload), 50):
        client.upsert(collection_name="jd", data=payload[start : start + 50])
    client.flush(collection_name="jd")
    print(f"已写入 {len(payload)} 行，当前行数 {count_documents('jd')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="按岗位边界重建 jd 语料")
    parser.add_argument("--dry-run", action="store_true", help="只切分不写库")
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
