"""工具包的公共类型（D32）。

一句话边界：**代码实现"能做什么"，页面上的 JSON 决定"这次怎么跑"**。

工具函数的契约是纯 JSON：

    async def run(input: dict, ctx: ToolContext) -> dict:
        args   = input["args"]     # 模型这次传的
        config = input["config"]   # 页面「配置 JSON」里存的值
        return {"ok": True, "summary": "一句话给模型看", "data": {...}}

平台**不按代码 schema 过滤参数**：页面上的「入参 JSON」才是权威，模型传什么就原样交给函数，
取不到值由函数自己返回 `{"ok": False, "summary": "缺少 query"}` 让模型改。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅为类型提示，避免循环导入
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models import Tool
    from app.llm.client import LLMClient


@dataclass
class ToolContext:
    """一次工具调用的运行时句柄：只放没法塞进 JSON 的活对象。"""

    tool: Tool
    config: dict[str, Any]
    db: AsyncSession
    llm: LLMClient | None = None
    system: str = ""
    user: str = ""
    #: 这次调用属于哪个会话。`recall` 这类"按存档指针取数"的工具必须有它 ——
    #: 指针编号（W1.T2）只在会话内唯一，不按会话过滤会一次查出多个会话的行。
    #: 测试场没有会话上下文，留空即可。
    #:
    #: **D50 起这就是指针的唯一作用域**：窗口号改成会话内跨回合累计（不再每回合从
    #: W1 重来），所以不需要再用 run_id 去区分"同一会话里的第几个回合"。
    session_id: int | None = None
    #: 这次调用属于哪个回合（run）。D50 之后**不再参与指针定位**，只留给排查与观测
    #: （"这一条是哪一轮问出来的"）。改造前它是指针的作用域，因为那时每个回合的
    #: 窗口号都从 W1 重新开始，同会话第二回合的 `W1.T2` 会和第一回合重名。
    run_id: int | None = None
    #: 本次回查可用的**字符**预算（D58）：`context_window × 阈值 × 50%`，由主循环算好传入。
    #: recall 自己拿不到模型的上下文窗口，也不知道 Agent 的换页阈值，只能由调用方给。
    #: 0 表示调用方没提供（测试场、旧调用点），此时 recall 退回一个保守的默认值。
    recall_budget_chars: int = 0


Runner = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


@dataclass
class ToolSpec:
    """代码里的一个工具实现。三个 schema 都只是**新建工具时的默认值**。"""

    impl_key: str
    title: str
    description: str
    kind: str  # llm | builtin
    default_params_schema: dict[str, Any]
    default_returns_schema: dict[str, Any]
    default_config: dict[str, Any]
    runner: Runner
    needs_prompt: bool = False
    default_timeout_s: int = 120
    meta: dict[str, Any] = field(default_factory=dict)
