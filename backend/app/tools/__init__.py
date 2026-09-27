"""工具包（D32）：一个 impl_key 一个模块，import 即注册。

新增工具：
    1. 在本目录加 `<impl_key>.py`，用 `@tool(...)` 装饰 `async def run(ctx, **params)`；
    2. 在下面的 import 列表里加一行；
    3. 重启后端 —— 工具页的「实现」下拉会自动出现它。

页面上的 `impl_key` 命中不到这里，工具页会标「未实现」，调用时返回一条观察结果而不是崩溃。
"""

from app.tools import (  # noqa: F401
    final_judge,
    http_request,
    interview_hr,
    interview_tech_lead,
    kb_search,
    pdf_extract,
    recall,
)
from app.tools.base import ToolContext, ToolSpec
from app.tools.registry import (
    TOOLS,
    build_tool_schema,
    execute_tool,
    get_impl,
    impl_status,
    list_impls,
    tool,
)

__all__ = [
    "TOOLS",
    "ToolContext",
    "ToolSpec",
    "build_tool_schema",
    "execute_tool",
    "get_impl",
    "impl_status",
    "list_impls",
    "tool",
]
