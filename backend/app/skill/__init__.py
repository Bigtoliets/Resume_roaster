"""Skill 蒸馏线路（SD 系列决策，见 docs/skill-distillation-plan.md）。

    store.py            文件层：读写 backend/skill/<name>/SKILL.md + 格式校验
    sync.py             skills 表与目录的一致性（启动对账 + 写路径 upsert）
    trace_source.py     取一个回合的完整轨迹（Langfuse v2 observations）
    distill_prompts.py  分析线路的提示词（代码常量，页面只读）
    distill.py          两轮调用 → 校验 → 原子写盘 → 后台任务入口

注意路径别混：**这个包是代码**（`app/skill/`），**skill 正文是数据**（`backend/skill/`）。
"""
