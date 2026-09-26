"""语料质检卡口：格式、岗位族映射、广告帖、Milvus 入库量。

用法：
    python scripts/check_corpus.py --jd data/sample/jd.jsonl
    python scripts/check_corpus.py --check-milvus

任一 FAIL 即退出码非 0 —— 该脚本是 M1 与演示前的卡口（方案 §10.4）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from job_families import JobFamilyError, normalize, prefix_for  # noqa: E402

MIN_JD = 40
MIN_INTERVIEW = 20

AD_KEYWORDS = ["培训", "包就业", "包分配", "招生", "加微信", "扫码", "付费内推", "课程咨询"]
REQUIREMENT_HINTS = ["任职要求", "岗位要求", "任职资格", "职位要求", "招聘要求"]


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def load(path: Path, report: Report) -> list[dict]:
    if not path.exists():
        report.error(f"文件不存在：{path}")
        return []
    items: list[dict] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            report.error(f"{path}:{lineno} JSON 解析失败：{exc}")
            continue
        for key in ("doc_id", "job_family", "raw_text"):
            if not str(item.get(key, "")).strip():
                report.error(f"{path}:{lineno} 缺少必需字段 {key}")
        doc_id = str(item.get("doc_id", "")).strip()
        if doc_id in seen:
            report.error(f"{path}:{lineno} doc_id 重复：{doc_id}（首次出现在第 {seen[doc_id]} 行）")
        seen[doc_id] = lineno
        try:
            normalize(str(item.get("job_family", "")))
        except JobFamilyError as exc:
            report.error(f"{path}:{lineno} {exc}")
        text = str(item.get("raw_text", ""))
        if len(text) < 100:
            report.warn(f"{path}:{lineno} 原文过短（{len(text)} 字），建议补全")
        items.append(item)
    return items


def check_jd(items: list[dict], path: Path, report: Report) -> None:
    for item in items:
        text = str(item["raw_text"])
        if not any(hint in text for hint in REQUIREMENT_HINTS):
            report.warn(
                f"{path}:{item['doc_id']} 未出现「任职要求」类段落，硬性门槛诊断会失真"
            )
        hits = [word for word in AD_KEYWORDS if word in text]
        if hits:
            report.error(f"{path}:{item['doc_id']} 疑似广告/招生帖（命中：{', '.join(hits)}）")
    if len(items) < MIN_JD:
        report.warn(
            f"JD 仅 {len(items)} 条，低于建议的最小可用量 {MIN_JD}；"
            "语料过少会让检索退化、辩论同质化（演示前建议补足）"
        )


def summarize(name: str, items: list[dict]) -> None:
    families = Counter(normalize(str(item["job_family"])) for item in items)
    print(f"\n{name}：{len(items)} 条")
    for family, count in families.most_common():
        print(f"  {family:<10} {count:>4} 条   id 前缀 = {prefix_for(family)}_")
    dates = sorted(str(item.get("publish_date", "")).strip() for item in items)
    dated = [d for d in dates if d]
    if dated:
        print(f"  发布时间：{dated[0]} ~ {dated[-1]}（{len(dated)}/{len(items)} 条填写）")


def check_milvus(report: Report) -> None:
    from app.core.config import settings

    if not settings.milvus_configured:
        report.warn("未配置 MILVUS_HOST，跳过向量库核对")
        return
    try:
        from app.vector.milvus_client import (
            COLLECTIONS,
            count_documents,
            get_client,
            server_version,
        )

        client = get_client()
        print(f"\nMilvus 服务端版本：{server_version()}")
        for name in COLLECTIONS:
            if not client.has_collection(name):
                report.error(f"缺少 collection：{name}（先跑 python scripts/init_collections.py）")
                continue
            # get_collection_stats 对未封存 segment 会返回 0，用 count(*) 才是真实行数
            row_count = count_documents(name)
            print(f"  {name:<10} 行数 {row_count}")
            if row_count == 0:
                report.warn(f"collection {name} 为空，检索会降级为仅简历诊断")
    except Exception as exc:  # noqa: BLE001
        report.error(f"Milvus 核对失败：{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="语料质检")
    parser.add_argument("--jd")
    parser.add_argument("--check-milvus", action="store_true", help="同时核对 Milvus 行数")
    args = parser.parse_args()
    if not args.jd and not args.check_milvus:
        parser.error("至少指定 --jd 或 --check-milvus")

    report = Report()
    if args.jd:
        jd_items = load(Path(args.jd), report)
        check_jd(jd_items, Path(args.jd), report)
        summarize("JD 语料", jd_items)
    if args.check_milvus:
        check_milvus(report)

    print("\n" + "=" * 60)
    for message in report.warnings:
        print(f"[WARN] {message}")
    for message in report.errors:
        print(f"[FAIL] {message}")
    if not report.errors and not report.warnings:
        print("全部检查通过。")

    if report.errors:
        print(f"\n{len(report.errors)} 项失败，必须修复后才能进入演示流程。")
        return 1
    if report.warnings:
        print(f"\n{len(report.warnings)} 项提醒（不阻塞）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
