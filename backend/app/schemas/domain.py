"""领域模型：简历画像、辩论发言、裁决、报告（对应方案 §5.2 / 附录 B）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal[
    "tech_depth",
    "business_impact",
    "expression",
    "credibility",
    "hard_requirement",
    "project_fit",
]
Speaker = Literal["tech_lead", "hrbp"]
Stance = Literal["pass", "borderline", "reject"]


class Anchor(BaseModel):
    """原文锚点：所有结论都必须能指回简历里的具体句子。"""

    field: str = ""
    text: str = ""
    start: int = 0
    end: int = 0


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    start: str = ""
    end: str = ""
    gpa: str | None = None


class ResumeBasic(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    intended_role: str | None = None
    graduation_year: int | None = None
    education: list[Education] = Field(default_factory=list)


class SkillItem(BaseModel):
    name: str
    claimed_level: str = ""
    evidence: str = ""
    mentions: int = 0


class Highlight(BaseModel):
    type: Literal["tech_depth", "business_metric", "engineering"] = "tech_depth"
    desc: str = ""
    evidence: str = ""
    has_metric: bool = False
    metric: str = ""


class DepthSignals(BaseModel):
    has_architecture_decision: bool = False
    has_tradeoff: bool = False
    has_troubleshooting: bool = False


class ProjectItem(BaseModel):
    name: str = ""
    role: str = ""
    period: str = ""
    team_size: int | None = None
    background: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    contribution_raw: str = ""
    highlights: list[Highlight] = Field(default_factory=list)
    depth_signals: DepthSignals = Field(default_factory=DepthSignals)
    anchors: list[Anchor] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    basic: ResumeBasic = Field(default_factory=ResumeBasic)
    skills: list[SkillItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    internships: list[dict[str, Any]] = Field(default_factory=list)
    awards: list[dict[str, Any]] = Field(default_factory=list)
    self_evaluation: str = ""
    red_flags: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    id: str
    category: Category = "tech_depth"
    severity: int = Field(default=3, ge=1, le=5)
    claim: str = ""
    resume_anchor: Anchor = Field(default_factory=Anchor)
    jd_evidence: dict[str, Any] = Field(default_factory=dict)
    suggestion: str = ""
    raised_by: Speaker | None = None
    round: int = 1
    status: Literal["open", "accepted", "dismissed"] = "open"


class Rebuttal(BaseModel):
    target_finding_id: str
    verdict: Literal["agree", "partial", "reject"] = "partial"
    reason: str = ""


class Utterance(BaseModel):
    round: int = 1
    speaker: Speaker
    stance: Stance = "borderline"
    findings: list[Finding] = Field(default_factory=list)
    rebuttals: list[Rebuttal] = Field(default_factory=list)
    new_finding_count: int = 0
    text: str = ""  # 流式展示用的口语化文本


class SupervisorCheck(BaseModel):
    round: int
    decision: Literal["continue", "terminate"] = "continue"
    coverage: dict[str, bool] = Field(default_factory=dict)
    uncovered: list[str] = Field(default_factory=list)
    open_disputes: list[str] = Field(default_factory=list)
    reason: str = ""
    new_findings_count: int = 0


class GateItem(BaseModel):
    """门槛对照项：字段给默认值，模型偶尔漏字段时不至于让整轮诊断崩掉。"""

    item: str = ""
    requirement: str = ""
    candidate: str = ""
    match: bool | None = None  # None = 未披露
    jd_ref: str = ""
    note: str = ""


class Verdict(BaseModel):
    pass_screening: bool | None = None
    confidence: float = 0.0
    headline: str = ""
    fatal_weaknesses: list[Finding] = Field(default_factory=list)
    disputes: list[str] = Field(default_factory=list)
    gate_comparison: list[GateItem] = Field(default_factory=list)


class RewriteAdvice(BaseModel):
    finding_id: str = ""
    original: str = ""
    rewritten: str = ""
    reason: str = ""
    resume_anchor: Anchor = Field(default_factory=Anchor)


class RoadmapWeek(BaseModel):
    week: int
    goal: str = ""
    deliverable: str = ""
    acceptance: str = ""
    topics: list[str] = Field(default_factory=list)


class DebateSummary(BaseModel):
    rounds: int = 0
    utterances: list[Utterance] = Field(default_factory=list)
    supervisor_checks: list[SupervisorCheck] = Field(default_factory=list)
    terminated_reason: str = ""


class ReportMeta(BaseModel):
    target_role: str = ""
    model_versions: dict[str, Any] = Field(default_factory=dict)
    langfuse_trace_id: str = ""
    langfuse_url: str = ""
    elapsed_ms: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    notices: list[str] = Field(default_factory=list)


class Report(BaseModel):
    headline: str = ""
    gate_comparison: list[GateItem] = Field(default_factory=list)
    fatal_weaknesses: list[Finding] = Field(default_factory=list)
    rewrites: list[RewriteAdvice] = Field(default_factory=list)
    roadmap: list[RoadmapWeek] = Field(default_factory=list)
    debate_summary: DebateSummary = Field(default_factory=DebateSummary)
    meta: ReportMeta = Field(default_factory=ReportMeta)
