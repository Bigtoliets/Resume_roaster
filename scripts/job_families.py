"""岗位族 → id 前缀映射。

id 前缀是唯一的"岗位族过滤键"（4 字段 collection 里没有独立的 job_family 字段），
集中在此处定义，避免手写前缀写歪导致整批检索不到。
"""

from __future__ import annotations

#: 岗位族中文名 -> id 前缀
JOB_FAMILIES: dict[str, str] = {
    "Java后端": "java",
    "大数据开发": "bigdata",
    "数据分析": "data",
}

#: 兼容常见写法，统一归一到上面的规范名
ALIASES: dict[str, str] = {
    "java后端开发": "Java后端",
    "java后端": "Java后端",
    "后端开发": "Java后端",
    "大数据开发实习生": "大数据开发",
    "大数据": "大数据开发",
    "数据分析师": "数据分析",
    "数据分析（偏ai应用）": "数据分析",
}


class JobFamilyError(ValueError):
    pass


def normalize(job_family: str) -> str:
    """把各种写法归一成规范岗位族名。"""
    raw = (job_family or "").strip()
    if raw in JOB_FAMILIES:
        return raw
    key = raw.lower().replace(" ", "")
    if key in ALIASES:
        return ALIASES[key]
    for canonical, prefix in JOB_FAMILIES.items():
        if prefix in key or canonical.lower() in key:
            return canonical
    raise JobFamilyError(
        f"未知岗位族：{job_family!r}。请在 scripts/job_families.py 里补充，"
        f"当前可选：{', '.join(JOB_FAMILIES)}"
    )


def prefix_for(job_family: str) -> str:
    return JOB_FAMILIES[normalize(job_family)]


def build_id(job_family: str, doc_id: str) -> str:
    return f"{prefix_for(job_family)}_{doc_id}"
