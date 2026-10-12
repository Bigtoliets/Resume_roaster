"""语料入库前的文本清洗与切块。

两件事，都是为了检索能真的按语义命中：

1. **去重**：线上那批 JD 是早期"按固定字数切 + 拼接回整篇"留下的，重叠部分
   变成了整块重复。实测 100 条里有 **81 条**正文整段重复——BM25 会把重复段
   当成"反复强调"，向量也会被重复内容拉偏。这里删掉紧挨着的第二份。
2. **切块**：一条 JD 里「岗位职责」和「任职要求」说的不是一回事，压成一个
   向量会互相稀释。按小标题切开，每块都带上抬头（方向 / 岗位名 / 薪资 / 技术标签），
   于是"Flink checkpoint 状态管理"能命中要求段，而不是和职责段取平均。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: 语料里出现过的小标题。它们常常**粘在上一句末尾**（"…研发工作量任职要求："），
#: 所以切分用 lookahead 在任何位置找，而不是只看行首。
SECTION_WORDS: tuple[str, ...] = (
    "岗位职责",
    "工作职责",
    "职位描述",
    "岗位描述",
    "任职要求",
    "任职资格",
    "岗位要求",
    "加分项",
    "其他说明",
    "其他要求",
    "福利待遇",
    "薪资福利",
)

#: 独占一行的小标题（"任职要求："）。切段按行判定，不在句子中间乱切——
#: "入职后要明确岗位职责与分工"这种正常句子必须原样保留。
_SECTION_HEAD_LINE_RE = re.compile(r"^(?:" + "|".join(SECTION_WORDS) + r")\s*[:：]?\s*$")

#: 抬头里的字段名。它们和小标题一样会被粘成一行
#: （"工作地点：昆明五华区经验要求：在校生实习学历要求：本科技术标签：Java、…"），
#: 不拆开的话，重复块的边界会落在一行中间，连续去重照样认不出来。
FIELD_LABELS: tuple[str, ...] = (
    "薪资范围",
    "工作地点",
    "经验要求",
    "学历要求",
    "技术标签",
    "职位类型",
    "招聘人数",
    "发布时间",
)

#: 用于断行的全部标签 = 小标题 + 抬头字段。
_BREAK_LABELS = tuple(dict.fromkeys(SECTION_WORDS + FIELD_LABELS))

#: 标签**粘在上一句末尾**的形态（"…研发工作量任职要求："）。只在后面跟着冒号时
#: 才断开。交替分支必须用 `(?:…)` 包起来，否则 `\s*[:：]` 只作用于最后一个分支，
#: "明确岗位职责与分工" 也会被从句中劈开。
_GLUED_HEAD_RE = re.compile(r"(?<!\n)(?=(?:" + "|".join(_BREAK_LABELS) + r")\s*[:：])")


def normalize_sections(text: str) -> str:
    """把小标题从句尾摘出来单独成行。

    这一步是去重的前置条件：重复块的**最后一行常常粘着下一段标题**，逐字比较
    就认不出是重复，连续去重会整块漏掉（实测 100 条里有 70 条是这种形态）。
    """
    return _GLUED_HEAD_RE.sub("\n", text)


def is_section_head(line: str) -> bool:
    """这一行是不是独占一行的小标题（"任职要求："）。"""
    return bool(_SECTION_HEAD_LINE_RE.match(line.strip()))


def dedupe_blocks(lines: Sequence[str]) -> list[str]:
    """删掉**紧挨着的**重复块（同一段被拼接了两次）。

    只比较首尾相邻的两份，不做全文去重：JD 里两条要求用词相近是正常的，
    全文去重会误删。判定从大到小试，先找最长的重复块，避免把大块拆成小块删错。
    """
    out: list[str] = []
    for line in lines:
        out.append(line)
        size = len(out) // 2
        while size >= 1:
            if out[-2 * size : -size] == out[-size:]:
                del out[-size:]
                break
            size -= 1
    return out


def clean_text(text: str) -> str:
    """入库前的唯一清洗入口：先把粘住的小标题摘出来，再删连续重复块。"""
    return "\n".join(dedupe_blocks(normalize_sections(text).split("\n"))).strip()


def split_sections(text: str) -> list[str]:
    """按独占一行的小标题切成语义段；没有小标题就整段返回。"""
    sections: list[str] = []
    buffer: list[str] = []
    for line in text.split("\n"):
        if _SECTION_HEAD_LINE_RE.match(line.strip()) and buffer:
            sections.append("\n".join(buffer).strip())
            buffer = [line.strip()]
        else:
            buffer.append(line)
    if buffer:
        sections.append("\n".join(buffer).strip())
    return [section for section in sections if section]


def _split_oversized(section: str, max_chars: int) -> list[str]:
    """超长语义段按行贪心切；单行就超长时只能硬切。"""
    chunks: list[str] = []
    buffer = ""
    for line in section.split("\n"):
        if len(line) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(
                line[start : start + max_chars] for start in range(0, len(line), max_chars)
            )
            continue
        if buffer and len(buffer) + len(line) + 1 > max_chars:
            chunks.append(buffer)
            buffer = ""
        buffer = f"{buffer}\n{line}".strip() if buffer else line
    if buffer:
        chunks.append(buffer)
    return chunks


def chunk_document(
    text: str,
    *,
    header: str = "",
    max_chars: int = 400,
    min_chars: int = 20,
) -> list[str]:
    """一条文档 → 若干块，每块都带抬头。

    抬头（方向 + 岗位名 + 薪资/城市/技术标签）**不计入** `max_chars`：它是上下文
    而不是内容。少了它，模型只看到"熟悉 Flink"却不知道是哪类岗位在要。
    """
    cleaned = clean_text(text)
    if not cleaned:
        return []

    pieces: list[str] = []
    for section in split_sections(cleaned):
        if len(section) <= max_chars:
            pieces.append(section)
        else:
            pieces.extend(_split_oversized(section, max_chars))

    merged: list[str] = []
    for piece in pieces:
        # 只剩标题的块一律并走；其余只有碎到没有信息量才并
        if merged and (len(piece) < min_chars or _SECTION_HEAD_LINE_RE.match(piece)):
            merged[-1] = f"{merged[-1]}\n{piece}"
        else:
            merged.append(piece)

    # 开头就是个光杆标题（"加分项："后面没内容）：并进后面那一块
    if len(merged) > 1 and _SECTION_HEAD_LINE_RE.match(merged[0]):
        merged[1] = f"{merged[0]}\n{merged[1]}"
        merged.pop(0)

    return [f"{header}\n{piece}" if header else piece for piece in merged]
