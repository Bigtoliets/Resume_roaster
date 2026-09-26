"""工具说明文本：**写死在代码里**，由后端注入到提示词的 `{{tools}}` 变量。

为什么单独放这里、而不从 tools 表渲染：
  - tools 表里存的是"机器契约"（name / params_schema / required），字段长度有限、结构固定，
    供 function calling 使用；
  - 提示词里需要的是"给模型看的用法说明"：什么时候用它、必填什么、返回长什么样、
    要等多久、会不会要人工确认、跟别的工具有什么先后关系。
  两者受众不同，所以这份文本单独维护，改完直接生效（不用动数据库）。

新增后端工具时，在 `TOOL_DOCS` 里补一条。漏补不会静默：`render_tools_text` 会把它标出来。
"""

from __future__ import annotations

from collections.abc import Iterable

#: key = 工具的 name（tools 表里的 name，不是 impl_key）
TOOL_DOCS: dict[str, str] = {
    "resume_pdf_extract": """- name: resume_pdf_extract
  用途: 读取附件里的简历正文（PDF / txt / md），返回纯文本。拿到文本后才能做后续分析。
  参数: path: string（必填）— 附件详情里的 stored_path，形如 uploads/2026/09/21/xxxx.pdf
  返回: {"ok": true, "summary": "已读取 N 个文件", "data": {"text": "简历正文"}}
  注意: 路径必须用系统给出的那一个，不要自己拼路径；读不到文件时不要重试同一个路径""",
    "kb_search_perf": """- name: kb_search_perf
  用途: 在绑定的知识库里做「语义 + 关键词」混合检索，用来拿目标岗位的真实硬性要求与技术考点。
       当前只绑定了 JD 语料库，检索不到面经。
  参数: queries: string[]（必填）— 1~5 条，每条一个角度，例如
         ["Java后端 实习 任职要求 学历", "Java后端 技术栈考点 高并发 数据库"]
         query: string（可选，单条兼容写法，与 queries 二选一）
         top_k: integer（可选）— 每个查询返回条数，默认取知识库配置
  返回: {"ok": true, "summary": "命中 N 条",
         "data": {"items": [{"id", "score", "retrieval", "matched_queries", "text"}]}}
         retrieval 取值 dense | bm25 | both：both 表示语义与关键词同时命中，通常最可信
  注意: 结果为空说明关键词太窄，换角度重试一次；连续失败就改用已有信息继续，并在结论里标注局限""",
    "interview_tech_lead": """- name: interview_tech_lead
  用途: 让大厂技术面试官针对「简历 + 岗位要求」挑技术深度问题，质疑会引用简历原句。
  参数: resume: string（必填）— 简历正文全文
         job_requirements: string（必填）— 必须来自 kb_search_perf 的检索结果，不能凭常识编
  返回: {"ok": true, "summary": "技术面评估完成", "data": {"text": "质疑列表"}}
  注意: 慢工具，单次 20~90 秒；成功拿到结果后不要重复调用。
        与 interview_hr 互不依赖，可以和它同时发起以节省时间""",
    "interview_hr": """- name: interview_hr
  用途: 让大厂 HRBP 检查硬性门槛（学历 / 到岗时间 / 实习时长）、个人贡献与量化产出、表达逻辑。
  参数: resume: string（必填）— 简历正文全文
         job_requirements: string（必填）— 与 interview_tech_lead 用同一份检索结果
  返回: {"ok": true, "summary": "HR 评估完成", "data": {"text": "评估意见"}}
  注意: 慢工具，单次 20~90 秒；与 interview_tech_lead 互不依赖，可同时发起""",
    "final_judge": """- name: final_judge
  用途: 聚合技术面试官与 HR 的意见，输出一句话判决、致命弱点 Top3 与最该先做的事。
  参数: resume: string（必填）— 简历正文全文
         tech_findings: string（必填）— interview_tech_lead 的原样返回
         hr_findings: string（必填）— interview_hr 的原样返回
  返回: {"ok": true, "summary": "综合判决完成", "data": {"text": "判决 + 弱点 + 建议"}}
  注意: 必须在拿到双方意见之后才能调用；单次 30~120 秒，只会调用一次""",
}

_MISSING_PREFIX = "以下工具没有在 app/agent/tool_docs.py 里登记说明，请补上："


def render_tools_text(names: Iterable[str] | None = None) -> str:
    """渲染工具清单文本，用于注入提示词的 `{{tools}}`。

    names 为空 → 输出全部已登记工具；给了 names → 只输出这批（保持传入顺序），
    未登记的会明确标出来，避免"Agent 勾了工具、提示词里却没有它"这种静默不一致。
    """
    if names is None:
        blocks = [doc.strip() for doc in TOOL_DOCS.values()]
    else:
        ordered = [str(name) for name in names]
        blocks = [TOOL_DOCS[name].strip() for name in ordered if name in TOOL_DOCS]
        missing = [name for name in ordered if name not in TOOL_DOCS]
        if missing:
            blocks.append(f"{_MISSING_PREFIX}{'、'.join(missing)}")

    return "\n\n".join(blocks) if blocks else "（当前没有可用工具，请直接回答）"
