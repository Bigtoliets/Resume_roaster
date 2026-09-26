"""生成一份用于演示/冒烟测试的简历 PDF。

刻意保留应届生简历的典型问题，好让红蓝辩论有东西可吵：
- 技术名词只列不解释（写了 Redis 却不提缓存异常场景）
- 项目成果没有量化指标（"优化了查询性能""提升了用户体验"）
- 背景描述长、个人贡献含糊（"参与""负责"混用）

用法：
    python scripts/make_demo_resume.py                     # 输出 data/sample/resume_demo.pdf
    python scripts/make_demo_resume.py -o /tmp/my.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LINES: list[tuple[str, bool]] = [
    ("张小明", True),
    ("电话：138-0000-0000｜邮箱：zhangxiaoming@example.com｜现居杭州", False),
    ("求职意向：大数据开发实习生｜2027 届本科", False),
    ("", False),
    ("教育经历", True),
    ("某某大学　计算机科学与技术　本科　2023.09 - 2027.06", False),
    ("主修课程：数据结构、操作系统、数据库原理、计算机网络；GPA 3.5/4.0", False),
    ("", False),
    ("专业技能", True),
    ("· 熟悉 Java、Python、SQL；了解 Scala", False),
    ("· 熟悉 Hadoop、Hive、Spark，了解 Flink", False),
    ("· 熟悉 MySQL，了解 Redis、Kafka", False),
    ("· 熟练使用 Git、Linux 常用命令", False),
    ("", False),
    ("项目经历", True),
    ("校园二手交易平台（2025.03 - 2025.06）　小组 4 人，负责后端开发", False),
    ("项目背景：校园内二手物品交易需求旺盛，但缺少统一平台，信息分散在多个群聊中，", False),
    ("交易双方难以建立信任，且缺乏评价机制，因此我们设计并实现了一个二手交易平台，", False),
    ("包含商品发布、订单管理、聊天沟通、信用评价等模块。", False),
    ("· 使用 Spring Boot + MySQL 搭建后端接口，负责商品与订单模块的开发；", False),
    ("· 使用 Redis 缓存热点商品数据，优化了查询性能；", False),
    ("· 使用 Kafka 异步处理订单消息，提升了系统的响应速度；", False),
    ("· 参与数据库表设计，编写了部分 SQL 查询语句。", False),
    ("", False),
    ("电商用户行为分析（2025.09 - 2025.12）　个人项目", False),
    ("· 使用 Python 爬取公开数据集，清洗后存入 Hive 表；", False),
    ("· 使用 Spark SQL 完成用户购买频次、商品类目偏好等指标统计；", False),
    ("· 使用 Tableau 制作可视化看板，展示分析结果。", False),
    ("", False),
    ("实习经历", True),
    ("某某科技有限公司　数据开发实习生　2026.07 - 2026.09", False),
    ("· 参与数据仓库日常任务的维护，处理上游数据异常；", False),
    ("· 编写 Hive SQL 支撑业务方取数需求。", False),
    ("", False),
    ("荣誉与证书", True),
    ("· 校级三等奖学金（2024）", False),
    ("· 全国大学生数学建模竞赛省赛二等奖（2025）", False),
    ("", False),
    ("自我评价", True),
    ("学习能力强，对大数据技术有浓厚兴趣，愿意接受挑战，希望在大数据方向长期发展。", False),
]


def build(output: Path) -> Path:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover - 兼容旧版本
        import fitz  # type: ignore[no-redef]

    output.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    y = 60.0

    for text, is_heading in LINES:
        if not text:
            y += 8
            continue
        size = 15 if is_heading else 11
        font = "china-s" if is_heading else "china-ss"
        try:
            page.insert_text((56, y), text, fontsize=size, fontname=font)
        except Exception:  # noqa: BLE001  字体缺失时退回内置字体
            page.insert_text((56, y), text, fontsize=size, fontname="helv")
        y += size + 8
        if y > 780:
            page = doc.new_page()
            y = 60.0

    doc.save(output)
    doc.close()
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="生成演示用简历 PDF")
    parser.add_argument(
        "-o",
        "--output",
        default="data/sample/resume_demo.pdf",
        help="输出路径（相对项目根目录）",
    )
    args = parser.parse_args()
    path = Path(args.output)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    build(path)
    print(f"已生成：{path}（{path.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
