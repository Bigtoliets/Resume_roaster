export interface ResourceBase {
  id: number
  name: string
  description: string
  is_enabled: boolean
  created_at: string
  updated_at: string
  [key: string]: unknown
}

export interface SessionRow {
  id: number
  agent_id: number | null
  title: string
  status: string
  config_mode: string
  langfuse_session_id: string
  last_route?: string
  summary?: string
  summary_upto_message_id?: number
  created_at: string
  updated_at: string
}

export interface MessageRow {
  id: number
  session_id: number
  role: string
  type: 'text' | 'file' | 'progress' | 'report' | 'debate_trace'
  content: Record<string, any>
  tokens: number
  latency_ms: number
  run_id: number | null
  langfuse_trace_id: string
  created_at: string
}

export interface AttachmentRow {
  id: number
  session_id: number
  filename: string
  size: number
  sha256: string
  created_at: string
}

export interface Anchor {
  field?: string
  text: string
  start: number
  end: number
}

export interface Finding {
  id: string
  category: string
  severity: number
  claim: string
  resume_anchor: Anchor
  jd_evidence: Record<string, any>
  suggestion: string
  raised_by?: 'tech_lead' | 'hrbp'
  round?: number
  status?: string
}

export interface Rebuttal {
  target_finding_id: string
  verdict: 'agree' | 'partial' | 'reject'
  reason: string
}

export interface Utterance {
  round: number
  speaker: 'tech_lead' | 'hrbp'
  stance: 'pass' | 'borderline' | 'reject'
  findings: Finding[]
  rebuttals: Rebuttal[]
  new_finding_count: number
  text?: string
}

export interface SupervisorCheck {
  round: number
  decision: 'continue' | 'terminate'
  coverage: Record<string, boolean>
  uncovered: string[]
  open_disputes: string[]
  reason: string
  new_findings_count: number
}

export interface GateItem {
  item: string
  requirement: string
  candidate: string
  match: boolean | null
  jd_ref?: string
}

export interface RewriteAdvice {
  finding_id: string
  original: string
  rewritten: string
  reason: string
  resume_anchor?: Anchor
}

export interface RoadmapWeek {
  week: number
  goal: string
  deliverable: string
  acceptance: string
  topics: string[]
}

export interface Report {
  headline: string
  summary?: string
  /** 纯文本报告：核心交付物，可直接复制或下载为 .txt */
  text?: string
  pass_screening?: boolean | null
  confidence?: number
  gate_comparison: GateItem[]
  fatal_weaknesses: Finding[]
  rewrites: RewriteAdvice[]
  roadmap: RoadmapWeek[]
  debate_summary: {
    rounds: number
    utterances: Utterance[]
    supervisor_checks: SupervisorCheck[]
    terminated_reason: string
  }
  meta: {
    target_role?: string
    langfuse_trace_id?: string
    langfuse_url?: string
    elapsed_ms?: number
    notices?: string[]
    /** 本次装配：每个节点实际用了哪个模型/Prompt/Skills/语料/工具 */
    assembly?: {
      node: string
      agent?: string
      model?: string
      prompt?: string
      persona?: string
      skills?: string[]
      knowledge_bases?: string[]
      tools?: string[]
      error?: string
    }[]
    /** 本次检索：每个语料库各命中多少条、耗时多少 */
    retrieval?: {
      knowledge_base: string
      collection: string
      top_k: number
      id_prefix?: string
      count: number
      elapsed_ms: number
    }[]
  }
}

export const CATEGORY_LABEL: Record<string, string> = {
  tech_depth: '技术深度',
  business_impact: '业务产出',
  expression: '表达逻辑',
  credibility: '可信度',
  hard_requirement: '硬性门槛',
  project_fit: '岗位匹配',
}
