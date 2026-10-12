"""L1 门卫（Q11 / Q20）：能抠到值就短路，抠不到必须放行。

这块的规矩是**宁可漏、不可错**：漏了只是多付一次 L3，抠错会把整轮诊断带偏。
所以测的重点不是"覆盖率"，而是"绝不出错误的值"。
"""

from __future__ import annotations

import pytest

from app.intent.rules import has_trigger_word, match_slot_rules


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("目标岗位是大数据开发实习生", "大数据开发实习生"),
        ("目标岗位：大数据开发实习生", "大数据开发实习生"),
        ("求职意向：Java 后端开发", "Java 后端开发"),
        ("帮我改简历，目标大数据开发实习生（附简历）", "大数据开发实习生"),
        ("我想投数据分析师", "数据分析师"),
        ("应聘算法工程师（实习）", "算法工程师"),
        ("目标职位是 C++ 开发工程师", "C++ 开发工程师"),
    ],
)
def test_slot_rule_extracts_role(text: str, expected: str) -> None:
    assert match_slot_rules(text).get("target_role") == expected


@pytest.mark.parametrize(
    "text",
    [
        "目标岗位是什么？",  # 问句不是陈述
        "目标岗位是啥",
        "目标是提升竞争力",  # 值是动作不是岗位
        "目标：这份简历",
        "我想投简历",  # 抠出来只剩"简历"
        "帮我看看简历",  # 没有岗位表达，压根不该命中槽位规则
        "好的，继续",
    ],
)
def test_slot_rule_refuses_to_guess(text: str) -> None:
    assert "target_role" not in match_slot_rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "帮我看看简历",
        "这份 JD 要求太高了",
        "面试该怎么准备",
        "帮我优化一下",
    ],
)
def test_trigger_words_open_the_gate(text: str) -> None:
    assert has_trigger_word(text) is True


@pytest.mark.parametrize("text", ["好的，继续", "谢谢", "大数据开发实习生", "嗯"])
def test_plain_talk_does_not_open_the_gate(text: str) -> None:
    assert has_trigger_word(text) is False
