"""意图层的全部可调参数。

刻意全部是**代码常量**：不落库、不上页面（设计共识 Q13 / Q14）。
改这些值等于改行为，所以每一项都要有单测兜着，别在运行时再读第二份真相。
"""

from __future__ import annotations

from enum import StrEnum

#: 槽位名。写进追问消息的 `content`，供下一轮续接（Q16）。
SLOT_TARGET_ROLE = "target_role"

#: 缺槽位时的追问话术。固定文案，不由模型生成（Q12）。
TARGET_ROLE_ASK = "为了给出准确诊断，请告诉我你的目标岗位（例如「大数据开发实习生」）。"

#: 意图层整体硬超时（秒）。超时按"没有槽位"降级放行，绝不拖死一轮（Q5）。
#:
#: 实测（deepseek-chat + 本机出口）：冷调用 3.0s、复用连接后 0.6~0.9s。原定的
#: 2~3s 会让**会话首轮**那一次必然超时——而首轮恰恰是最需要追问的一轮，等于
#: 把自己关掉。所以取 5s：覆盖冷启动，又留得住"卡死时必须放行"的兜底语义。
INTENT_TIMEOUT_S = 5.0

#: 同一个槽位最多追问一次；已在 pending 状态时不再追问（Q8 / Q16）。
MAX_ASK_PER_SLOT = 1


class Intent(StrEnum):
    """标签集只有这四类。只有 DIAGNOSE 会触发追问（Q8）。"""

    DIAGNOSE = "diagnose"
    FOLLOWUP = "followup"
    PLATFORM = "platform"
    CHITCHAT = "chitchat"
