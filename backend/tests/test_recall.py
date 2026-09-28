"""回查存档（D48 / D50 / D57）：指针作用域、入参形状，以及"不静默截断"。

三个点都是实测翻车过才补的：
  - 完全不限作用域 → `W1.T2` 在所有会话里都存在 → `MultipleResultsFound`，
    模型连试两次全失败，最后只能让用户"重新上传简历"；
  - 只按 `session_id` 过滤 → 同一会话的**第二个回合**开始撞（每个回合窗口号
    都从 W1 重新开始），同样报 `MultipleResultsFound`；
  - 只收单个 `pointer` → 模型天然写成 `{"pointers": [...]}`，第一次调用必然被拒。

**D50 把第二条的成因消掉了**：窗口号改成会话内跨回合累计，`(window_index, seq)`
在整个会话里唯一，所以作用域从 run 退回 session；run_id 只留作排查。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.db.models import Tool
from app.tools.base import ToolContext
from app.tools.recall import run as recall_run


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _RecordingDb:
    """记下每条 SQL，用来断言"到底有没有按会话过滤"。"""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.statements: list[str] = []
        self._rows = rows or []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
        return _Result(self._rows)


def make_ctx(session_id: int | None, db: Any = None, run_id: int | None = None) -> ToolContext:
    tool = Tool(name="recall", display_name="回查存档", impl_key="recall")
    return ToolContext(tool=tool, config={}, db=db, session_id=session_id, run_id=run_id)


async def test_requires_session_context() -> None:
    """工具测试场没有会话：要说清楚原因，而不是含糊地报"指针找不到"。"""
    result = await recall_run({"args": {"pointer": "W1.T2"}, "config": {}}, make_ctx(None))
    assert result["ok"] is False
    assert result["error"] == "no_session_context"


async def test_missing_pointer_parameter() -> None:
    db = _RecordingDb()
    result = await recall_run({"args": {}, "config": {}}, make_ctx(1, db))
    assert result["ok"] is False
    assert result["error"] == "missing_pointer"
    assert db.statements == []  # 参数不对就别去查库


async def test_query_is_scoped_to_session_and_not_run() -> None:
    """核心回归：按**会话**过滤，且不再按 run 过滤（D50）。

    改造前必须按 run_id 过滤：那时窗口号每个回合都从 W1 重来，同一会话里必然有多行
    `W1`，只按 session 过滤会让第二回合撞上第一回合（实测 `MultipleResultsFound`，
    整个诊断卡死）。D50 把窗口号改成会话内跨回合累计之后 `(window_index, seq)` 唯一，
    于是 run_id 退出定位 —— **SQL 里不该再出现它，出现就说明作用域没换干净**。
    """
    db = _RecordingDb()
    # 故意递一个 run_id：它必须被忽略
    await recall_run(
        {"args": {"pointers": ["W1.T2"]}, "config": {}}, make_ctx(63, db, run_id=4242)
    )
    assert len(db.statements) == 1
    assert "session_id = 63" in db.statements[0]
    assert "run_id = 4242" not in db.statements[0]


async def test_query_works_without_run_id() -> None:
    """拿不到 run_id 时（测试场、后台任务）照样按会话查，不会跨会话串数据。"""
    db = _RecordingDb()
    await recall_run({"args": {"pointers": ["W1.T2"]}, "config": {}}, make_ctx(77, db))
    assert "session_id = 77" in db.statements[0]


async def test_batch_depth_is_split_by_budget_without_dropping_items() -> None:
    """D57：点名的每条都给，深度按总预算摊 —— 条数越多每条越浅，但一条都不丢。"""
    from app.tools.recall import MIN_ITEM_CHARS, _per_item_chars

    assert _per_item_chars(8000, 1) == 8000  # 只要一条就吃满预算
    assert _per_item_chars(8000, 8) == 1000
    assert _per_item_chars(8000, 32) == MIN_ITEM_CHARS  # 摊到底也不低于下限
    # 刻意没有"单条硬上限"：预算多大单条就能给多大，否则 D58 的百分比预算会被静默压掉
    assert _per_item_chars(40000, 1) == 40000


async def test_truncated_item_reports_true_total_length() -> None:
    """D57：截断可以，**静默**截断不行 —— label 必须报原文真实字数。

    改造前 label 用的是 `len(截断后的文本)`，等于告诉模型"就这么多"。这是笔记里
    出现"截断/不全"、进而反复回查的根因（见 D49）。
    """
    row = SimpleNamespace(content={"text": "字" * 9000}, role="user")
    db = _RecordingDb(rows=[row])
    ctx = make_ctx(63, db)
    ctx.recall_budget_chars = 1000  # 预算只够 1000 字
    result = await recall_run({"args": {"pointer": "W1.M1"}, "config": {}}, ctx)
    item = result["data"]["items"][0]
    assert len(item["text"]) == 1000
    assert "原文共 9000 字" in item["label"]


async def test_ambiguous_pointer_is_reported_not_crashed() -> None:
    """命中多行时要说清楚原因，而不是让 SQLAlchemy 抛 MultipleResultsFound。"""
    db = _RecordingDb(rows=[object(), object()])
    result = await recall_run({"args": {"pointer": "W1.T2"}, "config": {}}, make_ctx(63, db, 1))
    assert result["ok"] is False
    assert result["error"] == "ambiguous_pointer"
    assert "多条记录" in result["summary"]


async def test_accepts_single_batch_and_comma_string() -> None:
    """三种写法都要认：单个、数组、逗号分隔的字符串。"""
    single = await recall_run({"args": {"pointer": "W1.T2"}, "config": {}}, make_ctx(1, _RecordingDb()))
    batch = await recall_run(
        {"args": {"pointers": ["W1.T2", "W1.T3"]}, "config": {}}, make_ctx(1, _RecordingDb())
    )
    comma = await recall_run(
        {"args": {"pointer": "W1.T2, W1.T3"}, "config": {}}, make_ctx(1, _RecordingDb())
    )
    for result in (single, batch, comma):
        assert result["ok"] is False
        assert result["error"] == "not_found"
    assert "W1.T2" in single["summary"]
    assert "W1.T3" in batch["summary"]
    assert "W1.T3" in comma["summary"]


async def test_execute_tool_threads_scope_into_context() -> None:
    """`execute_tool` 必须把 session_id **和 run_id** 一路带到 `ToolContext`。

    少带 run_id 时 recall 只能退化到会话作用域，同一会话第二回合就会撞指针。
    """
    from app.tools.registry import execute_tool, get_impl, tool

    seen: dict[str, Any] = {}
    if get_impl("_test_session_probe") is None:

        @tool(impl_key="_test_session_probe", title="探针", description="仅测试用")
        async def probe(input: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:  # noqa: ARG001
            seen["session_id"] = ctx.session_id
            seen["run_id"] = ctx.run_id
            return {"ok": True, "summary": "ok"}

    row = Tool(
        name="_test_session_probe",
        display_name="探针",
        impl_key="_test_session_probe",
        params_schema={"type": "object", "properties": {}},
        impl_config={},
    )
    result = await execute_tool(  # type: ignore[arg-type]
        None, tool=row, arguments={}, session_id=7, run_id=9
    )
    assert result["ok"] is True
    assert seen["session_id"] == 7
    assert seen["run_id"] == 9


async def test_recall_returns_result_only_without_arguments() -> None:
    """按指针回查只要**结果**，不回灌工具入参。

    实测：一条 `interview_tech_lead` 的入参有 4422 字（简历 3039 + JD 1297），
    而它真正的诊断结果只有 1563 字 —— 把入参一起回灌，等于回查一次搬运 74% 的重复内容，
    既白吞 token，又更快撞上换页阈值；而且那些原文模型能按各自指针单独回查。
    """
    row = SimpleNamespace(
        id=1,
        tool_name="interview_tech_lead",
        result={"ok": True, "data": {"text": "技术面诊断正文"}, "summary": "摘要"},
        summary="技术面诊断摘要",
        arguments={"resume": "张" * 3000, "job_requirements": "JD" * 700, "top_k": 5},
    )
    db = _RecordingDb(rows=[row])
    result = await recall_run(
        {"args": {"pointer": "W1.T2"}, "config": {}}, make_ctx(1, db, run_id=2)
    )

    assert result["ok"] is True
    item = result["data"]["items"][0]
    assert "arguments" not in item
    blob = json.dumps(item, ensure_ascii=False)
    assert "张" * 10 not in blob, "入参里的简历原文绝不能出现在回查结果里"
    assert "技术面诊断正文" in item["result"]
