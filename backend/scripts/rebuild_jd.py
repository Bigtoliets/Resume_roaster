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
#: 旧正文里已经带过一次的方向行；先摘干净，最后统一补一次，否则重建一次多一行。
DIRECTION_LINE_RE = re.compile(r"^【方向】.*$", re.M)
#: 正文里带的方向（分块抬头里就有）。优先用它，比从 markdown 标题里找稳——
#: 本脚本的输入是自己的输出，靠标题找会让方向归属**每次重建漂一点**。
INLINE_DIRECTION_RE = re.compile(r"^【方向】\s*(.+?)\s*$", re.M)
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
    """把线上现有的行拼回整篇 markdown。

    语料可能是"一篇一行"（`jd_001`），也可能是分块后的（`jd_001#2`）。分块的行
    先按块序拼回同一篇，并去掉后续块重复的抬头——否则重建一次抬头就叠一层、
    岗位正文还会被当成独立岗位切碎（**重复执行不幂等**）。

    抬头怎么认：同一个岗位的每一块都带着同一份抬头，所以"所有块共有的非空行"
    就是抬头，不必额外存元数据。
    """
    grouped: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        parent, _, suffix = str(row["id"]).partition("#")
        index = int(suffix) if suffix.isdigit() else 0
        grouped.setdefault(parent, []).append((index, str(row["content"])))

    documents: list[str] = []
    for parent in sorted(grouped, key=_sort_key):
        chunks = [text for _, text in sorted(grouped[parent], key=lambda item: item[0])]
        shared: set[str] | None = None
        for chunk in chunks:
            lines = {line for line in chunk.split("\n") if line.strip()}
            shared = lines if shared is None else shared & lines
        header = shared or set()
        parts = [chunks[0].strip()]
        for chunk in chunks[1:]:
            body = [line for line in chunk.split("\n") if line.strip() not in header]
            parts.append("\n".join(body).strip())
        documents.append("\n".join(part for part in parts if part).strip())
    return "\n".join(documents)


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
        inline = INLINE_DIRECTION_RE.search(body)
        body = DIRECTION_LINE_RE.sub("", body).strip()
        direction = (
            inline.group(1)
            if inline
            else next((name for pos, name in reversed(directions) if pos < start), "")
        )
        title_match = re.match(r"【岗位\s*\d+】\s*(.*)", body.split("\n", 1)[0])
        title = (title_match.group(1).strip() if title_match else "")[:80]
        job = {
            "id": f"jd_{int(number):03d}",
            "body": body,
            "title": title,
            "direction": direction,
        }
        current = merged.get(job["id"])
        if current is None or len(job["body"]) > len(current["body"]):
            merged[job["id"]] = job
    return [merged[key] for key in sorted(merged)]


def to_rows(jobs: list[dict[str, str]]) -> list[dict[str, str]]:
    """一条岗位 → 若干待向量化的块。

    抬头（方向 + 岗位标题行，含薪资/城市/经验/技术标签）**复制到每一块上**：
    少了它，模型只看到"熟悉 Flink"却不知道是哪类岗位在要。抬头不计入切块长度。

    必须先 `normalize_sections` 再切抬头——原始正文里字段标签是粘在标题行上的
    （"…全栈开发岗位职责：1. 参与…"），直接取第一行会把第一段职责也当成抬头，
    复制进每一块。
    """
    from app.vector.chunking import chunk_document, is_section_head, normalize_sections

    rows: list[dict[str, str]] = []
    for job in jobs:
        lines = normalize_sections(job["body"]).split("\n")
        head_end = len(lines)
        for index, line in enumerate(lines):
            if is_section_head(line):
                head_end = index
                break
        header_lines = [line.strip() for line in lines[:head_end] if line.strip()]
        if job["direction"]:
            header_lines.insert(0, f"【方向】{job['direction']}")
        header = "\n".join(header_lines)
        body = "\n".join(lines[head_end:]).strip()
        chunks = chunk_document(body, header=header)
        for index, chunk in enumerate(chunks, start=1):
            # 只有一块就沿用原 id，多块才加 #N —— 老配置里的 `id like "jd_0%"` 仍然有效
            row_id = job["id"] if len(chunks) == 1 else f"{job['id']}#{index}"
            rows.append({"id": row_id, "content": chunk, "title": job["title"]})
    return rows


async def main_async(dry_run: bool, source: str | None) -> int:
    from app.vector.chunking import clean_text
    from app.vector.milvus_client import TEXT_FIELD, count_documents, get_client

    client = get_client()
    rows = client.query(collection_name="jd", filter="", output_fields=[TEXT_FIELD], limit=1000)
    print(f"线上现有 {len(rows)} 行（count(*) = {count_documents('jd')}）")

    # 语料真源是**这份 markdown**，Milvus 只是派生物。倒过来（把自己的输出当输入）
    # 会越重建越丢：分块后的行拼不回原文，`【岗位 N】` 标记会被相邻块吞掉。
    # 实测这么丢过 7 个岗位（4/18/45/60/71/80/89）。
    source_path = Path(source) if source else (BACKUP if BACKUP.is_file() else None)
    if source_path is not None:
        if not source_path.is_file():
            print(f"语料源文件不存在：{source_path}")
            return 2
        markdown = source_path.read_text(encoding="utf-8")
        print(f"语料源：{source_path}（{len(markdown)} 字）")
    else:
        markdown = merge(rows)
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        BACKUP.write_text(markdown, encoding="utf-8")
        print(f"没有语料源文件，改从线上拼回：{len(markdown)} 字 -> 备份到 {BACKUP}")

    jobs = split_jobs(markdown)
    print(f"\n按 `【岗位 N】` 切出 {len(jobs)} 条岗位")
    lengths = [len(job["body"]) for job in jobs]
    print(f"  去重后平均 {sum(lengths) // len(lengths)} 字｜最短 {min(lengths)}｜最长 {max(lengths)}")

    # 残留重复是"静默失败"的典型：去重规则漏了某种形态，你看不到任何报错。
    # 所以每次都把它打出来，理想值恒为 0。注意要量**清洗后**的文本——
    # 量清洗前会把"还没去重"当成残留，报出假警报。
    residual = 0
    for job in jobs:
        lines = [line.strip() for line in clean_text(job["body"]).split("\n") if line.strip()]
        if len(lines) != len(set(lines)):
            residual += 1

    rows = to_rows(jobs)
    print(f"切成 {len(rows)} 块（平均 {len(rows) / len(jobs):.1f} 块/岗位）")
    print(f"  去重后仍有重复行的岗位：{residual}/{len(jobs)}（应为 0）")
    by_direction: dict[str, int] = {}
    for job in jobs:
        by_direction[job["direction"] or "（无方向）"] = by_direction.get(job["direction"] or "（无方向）", 0) + 1
    for name, count in by_direction.items():
        print(f"  {name}: {count} 条")
    print("\n前 3 块预览：")
    for row in rows[:3]:
        head = row["content"].replace("\n", " ")[:110]
        print(f"  {row['id']}  {head}…")

    if dry_run:
        print("\ndry-run：未改动 Milvus。")
        return 0

    from app.vector.embedding import get_embedding_service

    service = get_embedding_service()
    texts = [row["content"] for row in rows]
    print(f"\n重算 dense（{len(texts)} 条，provider={service.resolve_provider()}）…")
    dense_list = await service.embed_dense(texts)

    # 全量重建：先清空再写。**delete 之后必须 flush** —— 实测 delete 后紧接着
    # upsert 再一起 flush 时，删除会被后面的写入盖掉，旧行原样留下（行数会翻倍）。
    client.delete(collection_name="jd", filter='id != ""')
    client.flush(collection_name="jd")
    print(f"已清空 {len(rows)} 行旧数据")

    payload = [
        {"id": row["id"], "content": row["content"], "dense": dense}
        for row, dense in zip(rows, dense_list, strict=True)
    ]
    for start in range(0, len(payload), 50):
        client.upsert(collection_name="jd", data=payload[start : start + 50])
    client.flush(collection_name="jd")
    final = count_documents("jd")
    print(f"已写入 {len(payload)} 行，当前行数 {final}")
    if final != len(payload):
        # 静默的脏数据最难查：行数对不上就直接报错，别让它带着旧行上线
        print(f"⚠️  行数与写入量不符（期望 {len(payload)}）：可能有未清掉的历史行，请检查 delete 过滤条件")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="按岗位边界重建 jd 语料")
    parser.add_argument("--dry-run", action="store_true", help="只切分不写库")
    parser.add_argument(
        "--source",
        help=f"语料 markdown 源文件（不给就用 {BACKUP}；都没有才从 Milvus 拼回）",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run, args.source))


if __name__ == "__main__":
    sys.exit(main())
