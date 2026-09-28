"""任务笔记（D48）：指针格式与确定性校验。"""

from __future__ import annotations

from app.agent.notes import (
    POINTER_RE,
    Notes,
    Progress,
    Ref,
    normalize_notes,
    render_window_digest,
    verify_notes,
)


def make_notes(
    goal: str = "诊断简历",
    stage: list[str] | None = None,
    plan: list[str] | None = None,
) -> Notes:
    return Notes(
        goal=goal,
        progress=Progress(stage=stage if stage is not None else ["已读简历"]),
        plan=plan or [],
    )


def test_pointer_format() -> None:
    assert POINTER_RE.match("W2.M5")
    assert POINTER_RE.match("W2.T3")
    assert not POINTER_RE.match("tool#113")   # 主键形式不再使用
    assert not POINTER_RE.match("W2.5")       # 必须带类型标识
    assert not POINTER_RE.match("W2.X1")


def test_normalize_fills_empty_goal_from_previous() -> None:
    """模型漏写 goal 时沿用上一版，而不是让整个换页失败。"""
    previous = make_notes(goal="诊断简历")
    fixed = normalize_notes(Notes(goal="", progress=Progress()), previous)
    assert fixed.goal == "诊断简历"
    assert fixed.progress.stage == ["已读简历"]  # 空 stage 沿用上一版


def test_verify_only_blocks_when_goal_truly_missing() -> None:
    """上一版也没有 goal 时才算致命 —— 这才是真的丢信息。"""
    assert verify_notes(make_notes(goal=""), None) != []


def test_verify_rejects_lost_steps() -> None:
    """上一版"已完成步骤"、新版一条不剩 → 视为投影失败（做过的事不该被忘掉）。"""
    previous = make_notes(stage=["resume_pdf_extract 读完简历，870 字"])
    problems = verify_notes(make_notes(stage=[]), previous)
    assert any("progress.stage" in item for item in problems)


def test_normalize_drops_bad_pointers() -> None:
    """非法指针（模型常把文件路径填进来）直接丢，不因此否掉整个换页。"""
    notes = Notes(
        goal="g",
        progress=Progress(stage=["p"]),
        refs=[Ref(pointer="tool#113"), Ref(pointer="W1.T2")],
    )
    fixed = normalize_notes(notes, None)
    assert [ref.pointer for ref in fixed.refs] == ["W1.T2"]
    assert verify_notes(fixed, None) == []


def test_first_projection_has_no_previous() -> None:
    """首次换页：previous 为空是合法输入，不该报"结论丢失"。"""
    assert verify_notes(make_notes(), None) == []


def test_normalize_keeps_only_one_plan_item() -> None:
    """plan 是"下一步"不是路线图 —— 投影写多了只保留第一条。"""
    notes = make_notes(plan=["补 HR 视角", "汇总弱点", "写改写建议"])
    fixed = normalize_notes(notes, None)
    assert fixed.plan == ["补 HR 视角"]


def test_normalize_drops_non_tool_steps() -> None:
    """progress.stage 只允许记"真实工具干的事"。

    用户要的格式是「用什么工具干了什么事情」；实测投影会写
    "直接判断：FlavorBase 亮点为 RAG 召回率 41%→85.7%、Supervisor-Worker 编排…"
    这种既不是工具、又在罗列细节的条目。
    """
    notes = make_notes(
        stage=[
            "resume_pdf_extract 读到简历正文 3238 字",
            "直接判断：FlavorBase 亮点为 RAG 召回率 41%→85.7%、Supervisor-Worker 编排",
            "kb_search_perf 取到 3 条岗位要求",
        ]
    )
    fixed = normalize_notes(notes, None, tool_names={"resume_pdf_extract", "kb_search_perf"})
    assert fixed.progress.stage == [
        "resume_pdf_extract 读到简历正文 3238 字",
        "kb_search_perf 取到 3 条岗位要求",
    ]


def test_normalize_truncates_overlong_steps() -> None:
    """一条 = 一行摘要；抄成长段就截断，别把笔记撑成报告。"""
    notes = make_notes(stage=["interview_tech_lead " + "很长的细节" * 30])
    fixed = normalize_notes(notes, None, tool_names={"interview_tech_lead"})
    assert len(fixed.progress.stage[0]) <= 41  # STEP_MAX_CHARS + 省略号
    assert fixed.progress.stage[0].endswith("…")


def test_normalize_falls_back_when_all_steps_are_illegal() -> None:
    """全被过滤掉时沿用上一版 —— 不能让"条目全被丢"变成"校验失败、不换页"。"""
    previous = make_notes(stage=["resume_pdf_extract 读到简历 870 字"])
    notes = make_notes(stage=["直接判断：随便写了点东西"])
    fixed = normalize_notes(notes, previous, tool_names={"resume_pdf_extract"})
    assert fixed.progress.stage == ["resume_pdf_extract 读到简历 870 字"]
    assert verify_notes(fixed, previous) == []


def test_normalize_drops_fabricated_steps_for_tools_that_never_ran() -> None:
    """新增条目必须对应"本窗口真跑过、且留下指针的工具"。

    实测最严重的一次编造：窗口里只跑了 resume_pdf_extract 和 kb_search_perf，
    投影却记下 "interview_hr 判定硬门槛基本符合" —— 它把模型的**思考**
    （"接下来问 HR 面"）当成了**已完成**。下游模型信了笔记，去猜它的指针
    （W1.T6/T7/T8…），一个回合 11 次 recall、3 次报错。
    """
    notes = make_notes(
        stage=[
            "resume_pdf_extract 读到简历正文 870 字",
            "interview_hr 判定硬门槛基本符合，到岗天数缺失（待确认）",
            "final_judge 给出终裁判决与致命弱点 Top3",
        ]
    )
    fixed = normalize_notes(
        notes,
        None,
        tool_names={"resume_pdf_extract", "kb_search_perf", "interview_hr", "final_judge"},
        # 本窗口真实跑过的只有这两个
        window_tool_names={"resume_pdf_extract", "kb_search_perf"},
    )
    assert fixed.progress.stage == ["resume_pdf_extract 读到简历正文 870 字"]


def test_normalize_keeps_carried_steps_from_previous_note() -> None:
    """上一版记过的**工具**要放行 —— 它跑在更早的窗口里，本窗口看不到它。

    注意判定按工具名、不按整串：投影经常把沿用条目重新措辞
    （"读到简历正文 870 字（张小明，2027…" → "读到简历正文 870 字"），
    精确匹配会把它们全部误杀 —— 实测笔记里的步骤因此从 3 条缩到 1 条。
    """
    previous = make_notes(
        stage=["interview_tech_lead 产出 7 条技术质疑", "kb_search_perf 取到 3 条岗位要求"]
    )
    notes = make_notes(
        stage=[
            "interview_tech_lead 产出 7 条技术质疑（严重度 5）",  # 沿用但被重新措辞
            "kb_search_perf 取到 3 条岗位要求",  # 沿用
            "recall 取回简历原文",  # 本窗口新增且真跑过
        ]
    )
    fixed = normalize_notes(
        notes,
        previous,
        tool_names={"interview_tech_lead", "kb_search_perf", "recall"},
        window_tool_names={"recall"},
    )
    assert fixed.progress.stage == [
        "interview_tech_lead 产出 7 条技术质疑（严重度 5）",
        "kb_search_perf 取到 3 条岗位要求",
        "recall 取回简历原文",
    ]


def test_old_style_progress_string_is_coerced_not_rejected() -> None:
    """模型按旧习惯把 progress 写成字符串时必须降级吸收，不能让投影整个失败。

    失败一次的代价是"这次不换页、上下文继续涨"，比结构不整齐严重得多。
    """
    notes = Notes.model_validate(
        {"goal": "g", "progress": "已读简历，还在检索 JD", "plan": "补 HR 视角", "refs": []}
    )
    assert notes.progress.stage == ["已读简历，还在检索 JD"]
    assert notes.plan == ["补 HR 视角"]  # 字符串 plan 包成单元素


def test_middle_version_shape_is_merged_into_steps() -> None:
    """中间那版 {stage, done, missing} 也要能吸收：合并成步骤列表，别丢已完成的事。"""
    notes = Notes.model_validate(
        {
            "goal": "g",
            "progress": {"stage": "技术面完成", "done": ["读完简历"], "missing": ["到岗时间"]},
        }
    )
    assert notes.progress.stage == ["技术面完成", "读完简历", "到岗时间（待确认）"]


def test_steps_written_as_string_is_coerced() -> None:
    notes = Notes.model_validate({"goal": "g", "progress": {"stage": "读完简历"}})
    assert notes.progress.stage == ["读完简历"]


def test_window_digest_keeps_pointer_and_truncates() -> None:
    digest = render_window_digest(
        [
            {"pointer": "W1.T1", "kind": "tool", "text": "x" * 2000},
            {"pointer": "W1.M2", "kind": "thought", "text": "先读简历"},
            {"pointer": "W1.T3", "kind": "tool", "text": "   "},  # 空内容应被跳过
        ]
    )
    assert "[W1.T1] 工具结果：" in digest
    assert "[W1.M2] 思考：先读简历" in digest
    assert "W1.T3" not in digest
    assert len(digest) < 1400  # 单条截断到 600 字


def test_window_digest_marks_round() -> None:
    """轮次要写进记录：投影靠它判断"哪些是刚发生的"。"""
    digest = render_window_digest(
        [
            {"pointer": "W1.M1", "kind": "user", "text": "诊断简历"},        # 用户原话不带轮次
            {"pointer": "W1.T2", "kind": "tool", "text": "提取到 870 字", "round": 1},
            {"pointer": "", "kind": "thought", "text": "先看岗位要求", "round": 2},
        ]
    )
    assert "[W1.M1] 用户：诊断简历" in digest          # round=0 → 不加轮次标注
    assert "[W1.T2] (第1轮) 工具结果：提取到 870 字" in digest
    assert "[—] (第2轮) 思考：先看岗位要求" in digest     # 无指针的条目用 — 占位


def test_projection_prompt_forbids_paths_in_pointer() -> None:
    """实测踩过的坑：模型把文件路径填进 pointer，整条 ref 只能被丢弃。"""
    from app.agent.notes import PROJECTION_SYSTEM

    assert "文件路径" in PROJECTION_SYSTEM
    assert "W1.T3" in PROJECTION_SYSTEM  # 正面示例必须给出合法形态


def test_window_digest_names_the_tool() -> None:
    """摘要文本本身常常看不出是谁返回的 —— 实测把 HR 面结果记成了技术面。"""
    digest = render_window_digest(
        [{"pointer": "W1.T2", "kind": "tool", "tool": "interview_hr", "text": "硬门槛表…", "round": 4}]
    )
    assert "[W1.T2] (第4轮) 工具 interview_hr 结果：硬门槛表…" in digest


def test_projection_prompt_requires_tool_name_in_ref_summary() -> None:
    from app.agent.notes import PROJECTION_SYSTEM

    assert "哪个工具" in PROJECTION_SYSTEM


def test_render_marks_excerpt_not_content_truncation() -> None:
    """投影输入被截断时必须**明说是摘录**。

    实测事故：投影只看到 200 字预览（结尾是省略号），就把面试官的结果判成
    "内容被截断"，写进笔记；主模型照着笔记不停回查、重跑同一个面试官工具，
    12 轮预算全耗在找同一批材料上。而原文其实是完整的。
    """
    digest = render_window_digest(
        [{"pointer": "W2.T2", "kind": "tool", "tool": "interview_tech_lead", "text": "x" * 2000}]
    )
    assert "此处仅为投影输入的摘录" in digest
    assert "原文共 2000 字" in digest


def test_render_short_entry_has_no_excerpt_marker() -> None:
    digest = render_window_digest([{"pointer": "W1.T1", "kind": "tool", "text": "提取到 870 字"}])
    assert "摘录" not in digest
    assert digest == "[W1.T1] 工具结果：提取到 870 字"


def test_projection_prompt_forbids_claiming_truncation() -> None:
    from app.agent.notes import PROJECTION_SYSTEM

    # 提示词改写过几次，断言跟着对齐当前措辞 —— 这条守卫的不变量始终是同一个：
    # 投影**不许**把存档里的正常内容当成"被截断/不全"，否则主模型会照着去反复回查。
    assert "严禁误判结果截断" in PROJECTION_SYSTEM
    assert "禁止写「截断」" in PROJECTION_SYSTEM


def test_projection_prompt_forbids_pointing_refs_at_recall() -> None:
    """refs 指向 recall 的结果 = 让下个窗口"回查一次回查"，内容重复且更长。"""
    from app.agent.notes import PROJECTION_SYSTEM

    assert "严禁 refs 指向 recall" in PROJECTION_SYSTEM
    assert "禁止指向 recall 的返回结果" in PROJECTION_SYSTEM


async def test_project_notes_names_its_generation() -> None:
    """投影那次 LLM 调用必须在 trace 里叫 `notes-projection`。

    否则它和主循环的 `llm-call` 混在一起，"模型在思考"和"系统在做记忆换页"
    在 Langfuse 上根本区分不出来 —— 这正是这次要解决的问题。
    """
    from app.agent.notes import project_notes

    class FakeLLM:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def complete_json(
            self, messages: list[dict[str, str]], schema: type, *, name: str = "llm-call"
        ) -> Notes:
            self.calls.append({"name": name, "messages": messages, "schema": schema})
            return make_notes(goal="诊断简历", stage="已读简历")

    llm = FakeLLM()
    notes = await project_notes(llm=llm, previous=None, window_digest="[W1.T1] 工具结果：870 字")

    assert llm.calls[0]["name"] == "notes-projection"
    assert llm.calls[0]["schema"] is Notes
    assert notes.goal == "诊断简历"


async def test_project_notes_feeds_latest_thought_as_plan_source() -> None:
    """`plan` 是模型最新那次 think，所以必须把它单独喂给投影，别让投影器自己编计划。"""
    from app.agent.notes import project_notes

    class FakeLLM:
        def __init__(self) -> None:
            self.payload = ""

        async def complete_json(
            self, messages: list[dict[str, str]], schema: type, *, name: str = "llm-call"
        ) -> Notes:
            self.payload = messages[1]["content"]
            return make_notes()

    llm = FakeLLM()
    await project_notes(
        llm=llm,
        previous=None,
        window_digest="[W1.T1] 工具结果：870 字",
        latest_thought="已拿到技术面质疑，接下来补 HR 视角的硬性门槛与量化产出。",
    )
    assert "模型在本窗口的最后一次思考" in llm.payload
    assert "接下来补 HR 视角的硬性门槛与量化产出" in llm.payload


async def test_project_notes_marks_missing_thought_explicitly() -> None:
    """没有 think 时也要明说，否则投影会以为"没给"等于"可以随便编"。"""
    from app.agent.notes import project_notes

    class FakeLLM:
        def __init__(self) -> None:
            self.payload = ""

        async def complete_json(
            self, messages: list[dict[str, str]], schema: type, *, name: str = "llm-call"
        ) -> Notes:
            self.payload = messages[1]["content"]
            return make_notes()

    llm = FakeLLM()
    await project_notes(llm=llm, previous=None, window_digest="x", latest_thought="")
    assert "没有留下思考文本" in llm.payload
