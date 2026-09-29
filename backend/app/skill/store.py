"""Skill 文件层：读写 `backend/skill/<name>/SKILL.md`，以及格式校验（SD10 / SD20）。

一句话边界：**代码负责文件与格式，模型只负责返回文本**。

目录约定（SD5.1）：

    backend/skill/<name>/SKILL.md     必需：YAML frontmatter + Markdown 正文
    backend/skill/<name>/agents/      可选：Codex 扩展（本项目不生成，SD21）

`name` 必须与目录同名 —— 这是 Agent Skills 规范的硬要求，也是我们不信任模型
自己写 frontmatter 的原因（它可能每回合改一次名字，把目录和绑定一起搞乱）。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

#: skill 数据目录
SKILL_DIR = PROJECT_ROOT / "backend" / "skill"

#: 自动维护的经验 skill 名（同时也是目录名，规范要求两者一致，SD14）
EXPERIENCE_SKILL = "operation-experience"

#: 正文上限（字符）。≈5000 tokens，对齐规范里 level-2 的建议值（SD20）
BODY_MAX_CHARS = 8000

#: description 上限（规范：1–1024）
DESCRIPTION_MAX_CHARS = 1024

#: name 规范：仅小写字母/数字/连字符；不得首尾连字符；不得连续连字符
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: 匹配开头的 `---\n...\n---\n`
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)


class SkillFormatError(ValueError):
    """skill 文件格式不合法。校验失败一律降级——不写盘（SD19）。"""


@dataclass(frozen=True)
class SkillFile:
    name: str
    description: str
    body: str
    meta: dict[str, Any]
    path: Path


# ---------------------------------------------------------------- 校验


def validate_name(name: str) -> str:
    """校验并归一化 skill 名。非法直接抛 SkillFormatError。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise SkillFormatError("name 不能为空")
    if len(cleaned) > 64:
        raise SkillFormatError("name 不能超过 64 字符")
    if not _NAME_RE.match(cleaned):
        raise SkillFormatError(
            "name 只能是小写字母、数字、连字符（不得以连字符开头/结尾，不得连续连字符）：" + cleaned
        )
    return cleaned


def validate_description(description: str) -> str:
    cleaned = (description or "").strip()
    if not cleaned:
        raise SkillFormatError("description 不能为空——它是这个 skill 唯一的触发指针")
    if len(cleaned) > DESCRIPTION_MAX_CHARS:
        raise SkillFormatError(f"description 过长（{len(cleaned)} > {DESCRIPTION_MAX_CHARS} 字符）")
    return cleaned


def validate_body(body: str, *, max_chars: int = BODY_MAX_CHARS) -> str:
    cleaned = (body or "").strip()
    if not cleaned:
        raise SkillFormatError("正文不能为空")
    if len(cleaned) > max_chars:
        raise SkillFormatError(f"正文过长（{len(cleaned)} > {max_chars} 字符）")
    return cleaned


# ---------------------------------------------------------------- 解析 / 渲染


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆出 `(frontmatter dict, 正文)`。没有 frontmatter 或 YAML 非法都抛错。"""
    match = _FRONTMATTER_RE.match(text or "")
    if match is None:
        raise SkillFormatError("缺少 YAML frontmatter（文件必须以 --- 开头并闭合）")
    import yaml

    try:
        meta = yaml.safe_load(match.group(1))
    except Exception as exc:  # noqa: BLE001 - 任何 YAML 错误都算格式不合法
        raise SkillFormatError(f"frontmatter 不是合法 YAML：{exc}") from exc
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise SkillFormatError("frontmatter 必须是一组 key: value")
    return meta, text[match.end() :]


def render_frontmatter(meta: dict[str, Any]) -> str:
    import yaml

    dumped = yaml.safe_dump(
        meta, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).strip()
    return f"---\n{dumped}\n---\n"


def compose(meta: dict[str, Any], body: str) -> str:
    """把 frontmatter 与正文拼成完整的 SKILL.md 文本。"""
    return f"{render_frontmatter(meta)}\n{body.strip()}\n"


def normalize_skill_text(
    text: str, *, name: str, force_description: str | None = None
) -> str:
    """校验模型返回的整份 SKILL.md，并**强制写回 name**（SD14）。

    `force_description` 给了就把 description 也锁死 —— 蒸馏线路用它把触发指针
    固定在代码/文件手里，不让模型每回合顺手改写（它一改，什么时候该加载这个 skill
    就跟着飘，而这是跨回合才能察觉的那类偏差）。

    返回规范化后的完整文本；任何一项不过就抛 SkillFormatError，调用方据此降级。
    """
    expected = validate_name(name)
    meta, body = parse_frontmatter(text)
    # 模型写的 name 一律忽略：目录名、表里的 name、frontmatter 三处必须永远一致
    meta["name"] = expected
    if force_description is None:
        meta["description"] = validate_description(str(meta.get("description") or ""))
    else:
        # 锁定的 description：模型写的直接丢掉（它每次都会顺手改写，把触发指针改飘）
        meta["description"] = validate_description(force_description)
    return compose(meta, validate_body(body))


# ---------------------------------------------------------------- 读


def skill_path(name: str) -> Path:
    return SKILL_DIR / validate_name(name) / "SKILL.md"


def read_skill(name: str) -> SkillFile | None:
    """读一个 skill。文件不存在返回 None；格式不合法抛 SkillFormatError。"""
    try:
        path = skill_path(name)
    except SkillFormatError:
        return None
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    return SkillFile(
        name=str(meta.get("name") or name),
        description=str(meta.get("description") or ""),
        body=body.strip(),
        meta=meta,
        path=path,
    )


def read_skill_body(name: str) -> str | None:
    """给主 ReAct 装配用：只取正文（frontmatter 是元数据，不进 system prompt）。"""
    skill = read_skill(name)
    return skill.body if skill is not None else None


def scan_skills() -> tuple[list[SkillFile], list[str]]:
    """扫 `backend/skill/*/SKILL.md`，返回 `(合法列表, 出错说明列表)`。

    单个文件格式坏掉不能让整个启动失败——收集错误交给调用方记日志。
    """
    found: list[SkillFile] = []
    problems: list[str] = []
    if not SKILL_DIR.is_dir():
        return found, problems
    for path in sorted(SKILL_DIR.glob("*/SKILL.md")):
        try:
            skill = read_skill(path.parent.name)
            if skill is None:
                continue
            if skill.name != path.parent.name:
                problems.append(f"{path}：frontmatter 的 name 与目录名不一致（{skill.name}）")
                continue
            found.append(skill)
        except SkillFormatError as exc:
            problems.append(f"{path}：{exc}")
    return found, problems


# ---------------------------------------------------------------- 写


def _atomic_write(target: Path, text: str) -> None:
    """先写临时文件再 rename，避免半截文件（SD19）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def write_skill_text(name: str, text: str) -> Path:
    """写回整份 SKILL.md（蒸馏线路用）。校验不过就抛错，文件一字不动。"""
    normalized = normalize_skill_text(text, name=name)
    target = skill_path(name)
    _atomic_write(target, normalized)
    logger.info("skill 已写回：%s（%s 字符）", target, len(normalized))
    return target


def create_skill(name: str, description: str, body: str = "") -> Path:
    """从页面新建一个 skill（SD22：可新建、不可编辑）。目录已存在则拒绝。"""
    cleaned = validate_name(name)
    target = skill_path(cleaned)
    if target.exists():
        raise SkillFormatError(f"skill 已存在：{cleaned}")
    meta = {"name": cleaned, "description": validate_description(description)}
    text = compose(meta, validate_body(body or f"# {cleaned}\n\n（待补充）"))
    _atomic_write(target, text)
    logger.info("skill 已创建：%s", target)
    return target


#: 经验 skill 的 description —— **模型不许改**（SD14）。
#:
#: 它是这个 skill 唯一的触发指针：写得对不对，决定"什么时候该读它"。
#: 模型每回合顺手改写会让它随内容漂移（今天讲工具、明天讲笔记），而这类偏差
#: 要跨很多回合才看得出来，所以固定在代码/文件侧。
#:
#: 写法三条纪律：
#:   1. **先说作用** —— 这个 skill 用来干什么（照着踩过的坑做事、少重复试错）。
#:      "是什么"和"何时读"都不够，触发它的理由最终是"读了能少犯错"。
#:   2. **不列主题清单** —— 写成"工具协议、附件路径、指针编号…"就等于把当前正文的目录
#:      抄进描述，正文一改它就过期。这个 skill 的正文本来就一直在变。
#:   3. **只列触发分支** —— 描述"什么时候该看它"（动手前 / 报错 / 按说明做却不成），
#:      这类条件不随内容漂移，才是稳定的召回依据。
SEED_DESCRIPTION = (
    "Resume_roaster 的运行时经验库：照本项目已经踩过的坑与约定做事，少重复试错。"
    "动手之前，或出现与预期不符的现象（报错、重复劳动、按说明做却不成）时先读它。"
)


def seed_skill_text(name: str = EXPERIENCE_SKILL) -> str:
    """文件还没建立时的兜底内容（正常情况下仓库里已经有一份）。"""
    body = (
        "# Operation Experience\n\n"
        "本文件由蒸馏线路在每回合结束后自动维护：输入是当前全文 + 该回合的完整轨迹，\n"
        "输出是新的全文。\n\n"
        "## 条目格式（建议，可演化）\n\n"
        "### <一句话标题>\n\n"
        "- **现象**：当时发生了什么（指回轨迹里的具体一步）\n"
        "- **原因**：为什么会这样\n"
        "- **对策**：下次该怎么做\n\n"
        "（尚无积累。）\n"
    )
    return compose(
        {"name": validate_name(name), "description": SEED_DESCRIPTION},
        validate_body(body),
    )
