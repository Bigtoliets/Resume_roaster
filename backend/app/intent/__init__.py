"""意图层：主循环之前的页面参数归一化（不改变"谁来回话"）。

对外只有 `pipeline.resolve` 一个入口。设计共识见会话记录：
只做归一化 + 决定是否追问，不做路由；失败一律降级放行。
"""

from app.intent.pipeline import TurnIntent, resolve

__all__ = ["TurnIntent", "resolve"]
