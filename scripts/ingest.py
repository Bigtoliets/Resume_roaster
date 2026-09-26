"""语料导入：JSONL → id 生成 → 元数据抬头 → dense 编码 → Milvus（幂等写入）。

用法：
    python scripts/ingest.py --jd data/local/jd.jsonl
    python scripts/ingest.py --jd data/sample/jd.jsonl --dry-run

约定（方案 §5.4）：
- JSONL 只强制 doc_id / job_family / raw_text 三个字段；
- 可选元数据（company / title / city / publish_date / source_url / stage）会被拼成一行抬头写进
  正文，报告里依然能展示"引自某大厂 2026-08 的 JD"；
- id = {岗位族前缀}_{doc_id}，超长文本才拆行并加 #N 后缀；
- 幂等：先按 id 删除旧行再写入。

Milvus 侧 schema 是 id / content / dense / sparse：
写入时源文件的 `raw_text` 映射到 `content`，**sparse 不写** —— 它由 collection 上的
BM25 Function 从 content 自动生成（见 app/vector/milvus_client.py 的注释）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Windows 控制台默认 GBK，中文输出会乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from job_families import build_id, normalize  # noqa: E402

#: 抬头字段顺序（决定元数据可读性）
HEADER_FIELDS: list[tuple[str, str]] = [
    ("company", "公司"),
    ("title", "岗位"),
    ("city", "城市"),
    ("stage", "面试阶段"),
    ("publish_date", "发布"),
    ("source_url", "来源"),
]

MAX_CHARS = 6000  # BGE-M3 可吃 8192 token；这里按字符保守切分，超出才拆行


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"语料文件不存在：{path}")
    records: list[dict[str, Any]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno} JSON 解析失败：{exc}") from exc
        if not isinstance(item, dict):
            raise SystemExit(f"{path}:{lineno} 每行必须是 JSON 对象")
        for key in ("doc_id", "job_family", "raw_text"):
            if not str(item.get(key, "")).strip():
                raise SystemExit(f"{path}:{lineno} 缺少必需字段 {key}")
        records.append(item)
    return records


def build_header(item: dict[str, Any]) -> str:
    parts = [
        f"{label}：{item[key]}"
        for key, label in HEADER_FIELDS
        if str(item.get(key, "")).strip()
    ]
    return "【" + "｜".join(parts) + "】" if parts else ""


def split_long_text(text: str) -> list[str]:
    """超长文本按段落拼接切分，尽量不切断段落。"""
    if len(text) <= MAX_CHARS:
        return [text]
    paragraphs = [p for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(buffer) + len(paragraph) + 1 > MAX_CHARS and buffer:
            chunks.append(buffer.strip())
            buffer = ""
        buffer += paragraph + "\n"
    if buffer.strip():
        chunks.append(buffer.strip())
    return chunks or [text[:MAX_CHARS]]


def to_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """JSONL 记录 → 待向量化的行（一行为一篇文档，超长才拆）。"""
    rows: list[dict[str, Any]] = []
    for item in records:
        job_family = normalize(str(item["job_family"]))
        base_id = build_id(job_family, str(item["doc_id"]).strip())
        header = build_header(item)
        parts = split_long_text(str(item["raw_text"]).strip())
        for index, part in enumerate(parts, start=1):
            text = f"{header}\n{part}" if header else part
            row_id = base_id if index == 1 else f"{base_id}#{index}"
            rows.append({"id": row_id, "content": text, "job_family": job_family})
    return rows


async def ingest_collection(
    collection: str, records: list[dict[str, Any]], dry_run: bool
) -> None:
    from app.vector.embedding import get_embedding_service
    from app.vector.milvus_client import delete_by_ids, upsert_documents

    if not records:
        print(f"[{collection}] 无记录，跳过")
        return

    rows = to_rows(records)
    print(f"[{collection}] {len(records)} 篇文档 → {len(rows)} 行待写入")

    if dry_run:
        for row in rows[:3]:
            preview = row["content"].replace("\n", " ")[:110]
            print(f"  · {row['id']}  {preview}…")
        print(f"[{collection}] dry-run，未写入向量库")
        return

    service = get_embedding_service()
    provider = service.resolve_provider()
    if provider == "fake":
        print(
            "⚠️  当前 embedding provider = fake（未安装 FlagEmbedding），"
            "写入的是占位向量，检索不具语义意义。\n"
            '    安装真实编码器：pip install -e "backend[embedding]"'
        )

    texts = [row["content"] for row in rows]
    dense_list = await service.embed_dense(texts)
    payload = [
        {
            "id": row["id"],
            "dense": dense,
            # sparse 由 Milvus 的 BM25 Function 从 content 生成，这里不能写
            "content": row["content"],
        }
        for row, dense in zip(rows, dense_list, strict=True)
    ]

    delete_by_ids(collection, [row["id"] for row in rows])
    count = upsert_documents(collection, payload)
    print(f"[{collection}] 已写入 {count} 行（provider={provider}）")


async def main_async(args: argparse.Namespace) -> int:
    from app.core.config import settings

    if not settings.milvus_configured and not args.dry_run:
        print("未配置 MILVUS_HOST，请先填写 .env；若只想检查切分结果，可加 --dry-run。")
        return 2

    if args.jd:
        await ingest_collection("jd", read_jsonl(Path(args.jd)), args.dry_run)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 JD 语料到 Milvus")
    parser.add_argument("--jd", help="JD JSONL 路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印切分与 id，不写入")
    args = parser.parse_args()
    if not args.jd:
        parser.error("至少指定 --jd")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
