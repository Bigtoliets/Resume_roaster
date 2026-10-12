"""L1 门卫（Q11 / Q20）。

两类规则，命中含义完全不同：

- **槽位规则**：正则能直接抠出值 → 填槽并短路整层（零成本）。
- **触发词**：只判定"这句话可能在说诊断这件事" → 放行进 L3 继续抽槽位。

两类都没命中就是放行：意图层完全不介入，原样交给主循环。

这里刻意**宁可漏、不可错**：抠不出来的表达一律落到 L3，因为写错一个岗位
会把整轮诊断带偏，而漏掉一个只是多付一次小模型调用。
"""

from __future__ import annotations

import re

from app.intent.config import SLOT_TARGET_ROLE

#: 抓到这些值说明命中的是问句或残句，不是岗位名（"目标岗位是什么？"）。
_ROLE_STOPWORDS = frozenset(
    {
        "什么",
        "啥",
        "哪个",
        "哪一个",
        "什么岗位",
        "什么职位",
        "什么方向",
        "多少",
        "简历",
        "岗位",
        "职位",
    }
)

#: 值为"动作/评价"而不是岗位名时丢弃（"目标是提升竞争力"）。
_ROLE_REJECT_CONTAINS = (
    "提升",
    "提高",
    "增加",
    "拿到",
    "找到",
    "自己",
    "一下",
    "这份",
    "这个",
    "那个",
    "方面",
    "相关",
    "请问",
)

#: 抠出来后两侧要削掉的装饰词。
_ROLE_TRAILING = ("的简历", "简历", "方向", "岗位", "职位", "吗", "呢", "吧", "啊", "呀", "哦", "了")
_ROLE_LEADING = ("是", "为", "的", "了")

#: 岗位名的字符范围：到第一个分隔符为止，最长 30 字（含中文标点做边界）。
#: 首字符不许是空白，之后允许空格——否则 "Java 后端开发" 会被截成 "Java"。
_ROLE_BODY = (
    r"[^\s，。；;、,.!！?？：:（）()\[\]「」【】]"
    r"[^，。；;、,.!！?？：:（）()\[\]「」【】]{0,29}"
)

#: 槽位规则：命中即可直接填槽并短路（Q20 的"能抠到值的规则享受短路特权"）。
SLOT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        SLOT_TARGET_ROLE,
        re.compile(
            rf"(?:目标岗位|目标职位|意向岗位|应聘岗位|求职意向|目标)"
            rf"\s*(?:是|为)?\s*[:：]?\s*({_ROLE_BODY})"
        ),
    ),
    (
        SLOT_TARGET_ROLE,
        re.compile(rf"(?:想投|要投|投递|应聘|申请)\s*(?:的|岗位|职位)?\s*[:：]?\s*({_ROLE_BODY})"),
    ),
)

#: 触发词：只判定主题，命中后仍要继续抽槽位。小写比较，覆盖中英混写。
TRIGGER_WORDS: tuple[str, ...] = (
    "简历",
    "诊断",
    "优化",
    "润色",
    "改写",
    "jd",
    "岗位",
    "招聘",
    "面试",
    "面经",
    "offer",
    "求职",
    "投递",
    "竞争力",
    "匹配度",
)


def _clean_role(raw: str) -> str:
    """把正则抠到的片段削成岗位名；判断不了就返回空串（交给 L3）。"""
    value = raw.strip()
    changed = True
    while changed and value:
        changed = False
        for token in _ROLE_TRAILING:
            if value.endswith(token) and len(value) > len(token):
                value = value[: -len(token)].strip()
                changed = True
                break
    for token in _ROLE_LEADING:
        if value.startswith(token) and len(value) > len(token):
            value = value[len(token) :].strip()
            break
    if not value or value in _ROLE_STOPWORDS:
        return ""
    if len(value) > 20:
        return ""
    if any(token in value for token in _ROLE_REJECT_CONTAINS):
        return ""
    return value


def match_slot_rules(text: str) -> dict[str, str]:
    """跑一遍槽位规则，返回能直接确定的槽位值（目前只有 `target_role`）。"""
    found: dict[str, str] = {}
    for slot, pattern in SLOT_RULES:
        if found.get(slot):
            continue
        match = pattern.search(text)
        if match is None:
            continue
        value = _clean_role(match.group(1))
        if value:
            found[slot] = value
    return found


def has_trigger_word(text: str) -> bool:
    """是否像"在说诊断这件事"。命中只代表该往下抽槽位，不代表可以直接下判断。"""
    lowered = text.lower()
    return any(word in lowered for word in TRIGGER_WORDS)
