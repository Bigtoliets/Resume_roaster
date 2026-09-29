"""Skill 文件层（SD10 / SD14 / SD16 / SD20）。

这一层是蒸馏线路的地基：名字规范、frontmatter 解析、正文长度、原子写。
所有落盘测试都在 tmp_path 沙箱里跑，绝不碰仓库里的 backend/skill/。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.skill import store
from app.skill.distill import extract_json_object, strip_code_fence


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(store, "SKILL_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------- 名字规范


@pytest.mark.parametrize(
    "name",
    ["pdf-processing", "data-analysis", "a", "operation-experience", "abc-123"],
)
def test_valid_names(name: str) -> None:
    assert store.validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "PDF-Processing",  # 大写
        "-pdf",  # 首连字符
        "pdf-",  # 尾连字符
        "pdf--processing",  # 连续连字符
        "operation experience",  # 空格（实际踩过）
        "缓存方案拷问",  # 中文
        "a/b",  # 目录穿越
        "",
        "x" * 65,  # 超长
    ],
)
def test_invalid_names(name: str) -> None:
    with pytest.raises(store.SkillFormatError):
        store.validate_name(name)


# ---------------------------------------------------------------- frontmatter


def test_parse_frontmatter_ok() -> None:
    meta, body = store.parse_frontmatter("---\nname: a\ndescription: b\n---\n\n正文\n")
    assert meta == {"name": "a", "description": "b"}
    assert body.strip() == "正文"


def test_parse_frontmatter_requires_block() -> None:
    with pytest.raises(store.SkillFormatError):
        store.parse_frontmatter("# 没有 frontmatter\n")


def test_parse_frontmatter_rejects_non_mapping() -> None:
    with pytest.raises(store.SkillFormatError):
        store.parse_frontmatter("---\n- a\n- b\n---\n正文")


def test_description_with_colon_survives_yaml() -> None:
    """手写切分会被 description 里的冒号搞崩——这就是用真 YAML 解析器的理由。"""
    text = '---\nname: a\ndescription: "做什么：检查 X；何时用：出现 Y"\n---\n正文'
    meta, _ = store.parse_frontmatter(text)
    assert meta["description"] == "做什么：检查 X；何时用：出现 Y"


# ---------------------------------------------------------------- 校验与写回


def test_normalize_forces_name() -> None:
    """模型写什么 name 都会被代码改回固定值（SD14）。"""
    text = "---\nname: whatever-the-model-wrote\ndescription: 做什么\n---\n正文"
    out = store.normalize_skill_text(text, name="operation-experience")
    meta, _ = store.parse_frontmatter(out)
    assert meta["name"] == "operation-experience"


def test_normalize_rejects_empty_description() -> None:
    with pytest.raises(store.SkillFormatError):
        store.normalize_skill_text("---\nname: a\n---\n正文", name="a")


def test_normalize_rejects_long_body() -> None:
    text = f"---\nname: a\ndescription: b\n---\n{'x' * (store.BODY_MAX_CHARS + 1)}"
    with pytest.raises(store.SkillFormatError, match="正文过长"):
        store.normalize_skill_text(text, name="a")


def test_normalize_accepts_body_at_limit() -> None:
    text = f"---\nname: a\ndescription: b\n---\n{'x' * store.BODY_MAX_CHARS}"
    store.normalize_skill_text(text, name="a")


def test_write_is_atomic_and_leaves_no_tmp(sandbox: Path) -> None:
    target = store.write_skill_text(
        "demo", "---\nname: demo\ndescription: 演示\n---\n\n正文\n"
    )
    assert target.is_file()
    assert target.read_text(encoding="utf-8").startswith("---\n")
    assert not list(sandbox.rglob("*.tmp"))


def test_create_then_read_roundtrip(sandbox: Path) -> None:
    store.create_skill("demo-skill", "演示用", "# 标题\n\n内容")
    skill = store.read_skill("demo-skill")
    assert skill is not None
    assert skill.name == "demo-skill"
    assert skill.description == "演示用"
    assert skill.body.startswith("# 标题")


def test_create_refuses_existing(sandbox: Path) -> None:
    store.create_skill("dup", "第一次")
    with pytest.raises(store.SkillFormatError, match="已存在"):
        store.create_skill("dup", "第二次")


def test_read_missing_returns_none(sandbox: Path) -> None:
    assert store.read_skill("nope") is None
    assert store.read_skill_body("nope") is None


def test_scan_skills_collects_and_reports(sandbox: Path) -> None:
    store.create_skill("good-one", "合法")
    bad = sandbox / "bad-one"
    bad.mkdir()
    (bad / "SKILL.md").write_text("# 没有 frontmatter\n", encoding="utf-8")
    found, problems = store.scan_skills()
    assert [skill.name for skill in found] == ["good-one"]
    assert len(problems) == 1


def test_seed_text_is_valid() -> None:
    text = store.seed_skill_text()
    store.normalize_skill_text(text, name=store.EXPERIENCE_SKILL)
    assert store.EXPERIENCE_SKILL in text


def test_repo_seed_file_matches_rules() -> None:
    """仓库里那份 operation-experience/SKILL.md 必须真的合规。"""
    path = store.SKILL_DIR / store.EXPERIENCE_SKILL / "SKILL.md"
    assert path.is_file(), f"缺初始文件：{path}"
    text = path.read_text(encoding="utf-8")
    meta, body = store.parse_frontmatter(text)
    assert meta["name"] == store.EXPERIENCE_SKILL
    assert 0 < len(str(meta["description"])) <= store.DESCRIPTION_MAX_CHARS
    assert 0 < len(body.strip()) <= store.BODY_MAX_CHARS


# ---------------------------------------------------------------- 模型输出解析


def test_extract_json_object_plain() -> None:
    assert extract_json_object('{"load": ["a"]}') == {"load": ["a"]}


def test_extract_json_object_with_fence_and_prose() -> None:
    text = '好的，我需要这些：\n```json\n{"load": ["a", "b"]}\n```\n以上。'
    assert extract_json_object(text) == {"load": ["a", "b"]}


def test_extract_json_object_returns_none_on_garbage() -> None:
    assert extract_json_object("完全没有 JSON") is None
    assert extract_json_object("") is None


def test_strip_code_fence() -> None:
    assert strip_code_fence("```markdown\n---\nname: a\n---\n正文\n```") == "---\nname: a\n---\n正文"
    assert strip_code_fence("---\nname: a\n---\n正文") == "---\nname: a\n---\n正文"


# ---------------------------------------------------------------- 轨迹取数（SD2 / SD3）


def test_prune_keeps_snake_case_and_serialises_time() -> None:
    """键名要保住（蛇形），时间要转成字符串 —— 两个坑都踩过。"""
    from datetime import UTC, datetime

    from app.skill.trace_source import prune

    raw = [
        {
            "id": "x",
            "trace_id": "t",
            "type": "AGENT",
            "name": "react-turn",
            "start_time": datetime(2026, 9, 28, 15, 14, 32, tzinfo=UTC),
            "latency": 1.5,
            "level": "DEFAULT",
            "input": '{"query": "hi"}',
            "output": None,
            "metadata": {"应该": "被丢掉"},
        }
    ]
    item = prune(raw)[0]
    assert item["trace_id"] == "t"
    assert item["start_time"].startswith("2026-09-28T15:14:32")
    assert item["input"] == {"query": "hi"}
    assert "metadata" not in item
    json.dumps(prune(raw), ensure_ascii=False)  # 必须能直接序列化


def test_prune_rejects_camel_case_keys() -> None:
    """防呆：SDK 若换回"别名声明的 json 模式"，这里要炸 —— 而不是静默丢掉元数据。"""
    from app.skill.trace_source import prune

    with pytest.raises(RuntimeError, match="camelCase"):
        prune([{"startTime": "2026-01-01T00:00:00", "traceId": "t", "id": "x"}])


# ---------------------------------------------------------------- 提示词渲染（SD11 / SD12）


def test_prompts_render_without_leftover_placeholders() -> None:
    """占位符名字写错的话，模型会看到裸的 `{{FOO}}` —— 这种错必须在这里被拦住。"""
    from app.skill import distill_prompts as prompts

    round1 = prompts.render(prompts.ROUND1, CATALOG="- a: 甲")
    assert "{{" not in round1
    assert "- a: 甲" in round1

    round2 = prompts.render(
        prompts.ROUND2_USER,
        CATALOG="- a: 甲",
        LOADED="## a\n正文",
        SKILL_NAME=store.EXPERIENCE_SKILL,
        EXPERIENCE="---\nname: x\n---\n正文",
        TRACE="[]",
    )
    assert "{{" not in round2
    assert store.EXPERIENCE_SKILL in round2
    assert "## a" in round2

    feedback = prompts.render(prompts.TOO_LONG_FEEDBACK, ACTUAL=9000, LIMIT=8000)
    assert "{{" not in feedback
    assert "9000" in feedback and "8000" in feedback


def test_round1_asks_for_every_matching_skill() -> None:
    """按需取是"满足要求的都要"，不是只挑一个（实测写成单数会把模型带偏）。"""
    from app.skill import distill_prompts as prompts

    # 措辞可以改，但两件事必须一直在：例子里是**多个**元素，且明确要求"全部列出"
    assert "全部列出" in prompts.ROUND1
    assert '"skill-name-1", "skill-name-2"' in prompts.ROUND1


# ---------------------------------------------------------------- 主 ReAct 装配（SD15）


async def test_system_prompt_reads_skill_body_from_file(sandbox: Path) -> None:
    """主 ReAct 的 skill 正文来自文件，而不是数据库（SD15）。"""
    from typing import Any

    from app.agent.loop import build_system_prompt
    from app.db.models import Agent, Prompt, Skill

    store.create_skill("demo-skill", "演示", "# 只有文件里才有的正文\n\n必须追问缓存穿透。")

    class FakeDB:
        async def get(self, model: Any, _key: int) -> Any:
            if model is Skill:
                return Skill(id=1, name="demo-skill", description="演示", is_enabled=True)
            if model is Prompt:
                return None
            return None

    agent = Agent(id=1, name="测试官", skill_ids=[1], tool_ids=[], max_rounds=3)
    text = await build_system_prompt(FakeDB(), agent)  # type: ignore[arg-type]
    assert "【Skill：demo-skill】" in text
    assert "必须追问缓存穿透" in text


async def test_system_prompt_skips_missing_skill_file(sandbox: Path) -> None:
    """文件被删/改名时只跳过并告警，不能让整条对话挂掉。"""
    from typing import Any

    from app.agent.loop import build_system_prompt
    from app.db.models import Agent, Skill

    class FakeDB:
        async def get(self, model: Any, _key: int) -> Any:
            if model is Skill:
                return Skill(id=9, name="no-such-file", description="", is_enabled=True)
            return None

    agent = Agent(id=1, name="测试官", skill_ids=[9], tool_ids=[], max_rounds=3)
    text = await build_system_prompt(FakeDB(), agent)  # type: ignore[arg-type]
    assert "no-such-file" not in text
