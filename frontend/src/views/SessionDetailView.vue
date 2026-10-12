<script setup lang="ts">
/** 唯一会话页：左侧历史会话 + 右侧 ReAct 对话（/sessions 与 /sessions/:id 共用）。 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, ApiError, streamTurn } from '@/api/client'
import type { AttachmentRow, MessageRow, Report, RunNoteRow, SessionRow } from '@/api/types'
import MarkdownBlock from '@/components/MarkdownBlock.vue'
import ReportTextView from '@/components/report/ReportTextView.vue'
import RunNoteCard from '@/components/RunNoteCard.vue'
import StepsTimeline from '@/components/StepsTimeline.vue'
import { confirmDialog } from '@/composables/useDialog'
import { describeError } from '@/composables/useResource'

interface Step {
  type: 'thought' | 'action' | 'observation' | 'approval'
  text?: string
  tool?: string
  tool_display?: string
  arguments?: Record<string, unknown>
  tool_call_id?: number | null
  summary?: string
  status?: string
  // ---- 仅 approval 用 ----
  tool_hint?: string
  remaining_s?: number
  timeout_s?: number
}

const props = defineProps<{ id?: string }>()
const router = useRouter()
const route = useRoute()

const sessions = ref<SessionRow[]>([])
const sessionId = ref<number | null>(props.id ? Number(props.id) : null)
const session = computed(() => sessions.value.find((item) => item.id === sessionId.value) || null)

const messages = ref<MessageRow[]>([])
const attachment = ref<AttachmentRow | null>(null)
/** 任务笔记（D48）：上下文换页时后端写一条，这里渲染成「任务进度卡」。 */
const notes = ref<RunNoteRow[]>([])
const loading = ref(false)

const agents = ref<{ id: number; name: string; tool_ids?: number[] }[]>([])
const currentAgentId = ref<number | null>(null)

const input = ref('')
const sending = ref(false)
const uploading = ref(false)
const error = ref('')
const search = ref('')

const liveSteps = ref<Step[]>([])
const liveTokens = ref('')
const liveStage = ref('')
/** 进行中已用秒数：长任务要能看出"还在跑"，否则像死机 */
const elapsed = ref(0)
let timer: number | null = null
const scrollAnchor = ref<HTMLElement | null>(null)

function startTimer() {
  elapsed.value = 0
  if (timer !== null) window.clearInterval(timer)
  timer = window.setInterval(() => {
    elapsed.value += 1
    // 待确认卡片的倒计时：每秒把 pending 的那几张减一
    for (const step of liveSteps.value) {
      if (step.type === 'approval' && step.status === 'pending' && (step.remaining_s ?? 0) > 0) {
        step.remaining_s = (step.remaining_s ?? 0) - 1
      }
    }
  }, 1000)
}

function stopTimer() {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

const sessionCache = new Map<
  number,
   {
     at: number
     messages: MessageRow[]
     attachment: AttachmentRow | null
     agentId: number | null
     notes: RunNoteRow[]
   }
>()
const CACHE_TTL = 60_000

const grouped = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  const buckets: Record<string, SessionRow[]> = {}
  const now = new Date()
  const today = now.toDateString()
  const yesterday = new Date(now.getTime() - 86400000).toDateString()
  for (const item of sessions.value) {
    if (keyword && !item.title.toLowerCase().includes(keyword)) continue
    const created = new Date(item.created_at)
    const key =
      created.toDateString() === today ? '今天' : created.toDateString() === yesterday ? '昨天' : '更早'
    ;(buckets[key] = buckets[key] || []).push(item)
  }
  return ['今天', '昨天', '更早']
    .filter((key) => buckets[key]?.length)
    .map((key) => ({ key, items: buckets[key] }))
})

const visibleMessages = computed(() =>
  messages.value.filter((item) => item.type === 'text' || item.type === 'file'),
)
const legacyReports = computed(() => messages.value.filter((item) => item.type === 'report'))

function textOf(message: MessageRow): string {
  return String((message.content as { text?: string })?.text || '')
}

function stepsOf(message: MessageRow): Step[] {
  return ((message.content as { steps?: Step[] })?.steps || []) as Step[]
}

function agentNameOf(message: MessageRow): string {
  return String((message.content as { agent_name?: string })?.agent_name || '')
}

/** 追问轮（后端 `content.kind === 'ask'`）换一种气泡，别混在正常回答里。 */
function kindOf(message: MessageRow): string {
  return String((message.content as { kind?: string })?.kind || '')
}

async function scrollToBottom(smooth = false) {
  await nextTick()
  scrollAnchor.value?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'end' })
}

async function loadSessions(): Promise<SessionRow[]> {
  sessions.value = await api.get<SessionRow[]>('/sessions')
  return sessions.value
}

async function loadAgents() {
  try {
    agents.value = await api.get<{ id: number; name: string; tool_ids?: number[] }[]>('/agents')
  } catch {
    agents.value = []
  }
}

async function loadMessages() {
  if (sessionId.value === null) return
  const target = sessionId.value
  const cached = sessionCache.get(target)
  if (cached && Date.now() - cached.at < CACHE_TTL) {
    messages.value = cached.messages
    attachment.value = cached.attachment
    currentAgentId.value = cached.agentId
     notes.value = cached.notes
    return
  }
  loading.value = true
  try {
     const [msgs, attachments, noteRows] = await Promise.all([
      api.get<MessageRow[]>(`/sessions/${target}/messages`),
      api.get<AttachmentRow[]>(`/sessions/${target}/attachments`).catch(() => []),
       // 笔记接口可能还没上线（旧后端）——失败就走空列表，不影响对话本身
       api.get<RunNoteRow[]>(`/sessions/${target}/notes`).catch(() => [] as RunNoteRow[]),
    ])
    if (sessionId.value !== target) return
    messages.value = msgs
    attachment.value = attachments[0] ?? null
     notes.value = noteRows
    const row = sessions.value.find((item) => item.id === target)
    currentAgentId.value = row?.agent_id ?? null
    sessionCache.set(target, {
      at: Date.now(),
      messages: msgs,
      attachment: attachment.value,
      agentId: currentAgentId.value,
       notes: noteRows,
    })
  } finally {
    if (sessionId.value === target) loading.value = false
  }
}

async function openTarget(id?: number | null) {
  if (id && id === sessionId.value && messages.value.length) return
  error.value = ''
  loading.value = true
  const list = sessions.value.length ? sessions.value : await loadSessions()
  let target = id ?? sessionId.value
  if (!target || !list.some((item) => item.id === target)) target = list[0]?.id ?? null
  if (target === null) {
    // 只有点「＋ 新建对话」才创建会话 —— 进页面不再自动建空会话
    sessionId.value = null
    messages.value = []
    attachment.value = null
     notes.value = []
    loading.value = false
    return
  }
  sessionId.value = target
  if (Number(route.params.id) !== target) await router.replace(`/sessions/${target}`)
  await loadMessages()
  await scrollToBottom()
}

async function newSession() {
  const created = await api.post<SessionRow>('/sessions', {
    title: '新会话',
    agent_id: currentAgentId.value ?? agents.value.find((a) => a.tool_ids?.length)?.id ?? null,
  })
  await loadSessions()
  sessionId.value = created.id
  await router.replace(`/sessions/${created.id}`)
  messages.value = []
  attachment.value = null
   notes.value = []
  input.value = ''
  await scrollToBottom()
}

async function switchAgent(event: Event) {
  const next = Number((event.target as HTMLSelectElement).value) || null
  currentAgentId.value = next
  if (sessionId.value === null) return
  try {
    await api.put(`/sessions/${sessionId.value}`, { agent_id: next })
    sessionCache.delete(sessionId.value)
    await loadSessions()
  } catch (exc) {
    error.value = describeError(exc)
  }
}

async function removeSession(id: number) {
  const target = sessions.value.find((item) => item.id === id)
  if (!target) return
  const ok = await confirmDialog({
    title: `删除会话「${target.title}」？`,
    description: '会话里的消息与工具调用记录会一并删除，且无法撤销。',
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  const snapshot = sessions.value
  sessions.value = sessions.value.filter((item) => item.id !== id)
  try {
    await api.del(`/sessions/${id}`)
    sessionCache.delete(id)
    if (sessionId.value === id) await openTarget(sessions.value[0]?.id ?? null)
    else await loadSessions()
  } catch (exc) {
    sessions.value = snapshot
    error.value = describeError(exc)
  }
}

async function renameSession() {
  if (!session.value) return
  const name = await promptRename(session.value.title)
  if (!name) return
  if (session.value) session.value.title = name
  try {
    await api.put(`/sessions/${sessionId.value}`, { title: name })
    await loadSessions()
  } catch (exc) {
    error.value = describeError(exc)
  }
}

async function promptRename(current: string): Promise<string | null> {
  const { promptDialog } = await import('@/composables/useDialog')
  return promptDialog({
    title: '重命名会话',
    description: '名字只影响左侧列表显示。',
    label: '会话名称',
    value: current,
    required: true,
  })
}

async function send() {
  const content = input.value.trim()
  if (sending.value || sessionId.value === null) return
  if (!content && !attachment.value) {
    error.value = '请输入内容，或先上传简历附件'
    return
  }
  sending.value = true
  error.value = ''
  liveSteps.value = []
  liveTokens.value = ''
  liveStage.value = '正在思考'
  startTimer()
  input.value = ''

  messages.value = [
    ...messages.value,
    {
      id: -Date.now(),
      session_id: sessionId.value,
      role: 'user',
      type: 'text',
      content: { text: content },
       // 本地乐观插入的占位消息：坐标要等后端落库后才有，先填 0
       window_index: 0,
       seq: 0,
      tokens: 0,
      latency_ms: 0,
      run_id: null,
      langfuse_trace_id: '',
      created_at: new Date().toISOString(),
    } as MessageRow,
  ]
  await scrollToBottom()

  try {
    await streamTurn(
      sessionId.value,
      { content, agent_id: currentAgentId.value, attachment_id: attachment.value?.id ?? null },
      async (event, data) => {
        if (event === 'stage') {
           // 换页事件（D48）：告诉用户"上下文满了、已经带着笔记开新窗口"，
           // 否则长任务里会出现"它怎么突然不记得刚才的事了"的困惑
           liveStage.value =
             data.stage === 'rollover'
               ? `上下文已换页（窗口 W${data.window_index ?? ''}），按任务笔记继续`
               : String(data.agent ? `${data.agent} 正在思考` : '正在思考')
        } else if (event === 'step') {
          const incoming = data as unknown as Step
          // 确认卡片有两次推送：先是 approval_required（等待中，临时渲染），
          // 决策完成后后端再推一条同 tool_call_id 的"已决"卡片——这里做替换，避免并排两张。
          const index =
            incoming.type === 'approval' && incoming.tool_call_id
              ? liveSteps.value.findIndex(
                  (item) => item.type === 'approval' && item.tool_call_id === incoming.tool_call_id,
                )
              : -1
          if (index >= 0) {
            liveSteps.value = liveSteps.value.map((item, i) =>
              i === index ? { ...item, ...incoming } : item,
            )
          } else {
            liveSteps.value = [...liveSteps.value, incoming]
          }
          await scrollToBottom(true)
        } else if (event === 'token') {
          liveTokens.value += String(data.delta || '')
          void scrollToBottom(true)
        } else if (event === 'assistant') {
          liveTokens.value = String(data.content || liveTokens.value)
          liveSteps.value = (data.steps as unknown as Step[]) || liveSteps.value
        } else if (event === 'approval_required') {
          // 不再弹浮层：把确认卡片**插进步骤流**，嵌在这一轮的思考/行动之间
          liveSteps.value = [
            ...liveSteps.value,
            {
              type: 'approval',
              tool: String(data.tool || ''),
              tool_display: String(data.tool_display || data.tool || ''),
              tool_hint: String(data.tool_hint || ''),
              arguments: (data.arguments as Record<string, unknown>) || {},
              tool_call_id: Number(data.tool_call_id),
              remaining_s: Number(data.remaining_s ?? data.timeout_s ?? 300),
              timeout_s: Number(data.timeout_s ?? 300),
              status: 'pending',
              text: '',
              call_id: '',
              summary: '',
            } as Step,
          ]
          await scrollToBottom(true)
        } else if (event === 'error') {
          error.value = `${data.code ?? 'ERROR'}：${data.msg ?? ''}`
        }
      },
    )
  } catch (exc) {
    error.value =
      exc instanceof ApiError && exc.status === 404
        ? '后端没有 /messages 接口（正在跑旧代码）。请重启后端。'
        : describeError(exc)
  } finally {
    stopTimer()
    sending.value = false
    liveSteps.value = []
    liveTokens.value = ''
    if (sessionId.value !== null) sessionCache.delete(sessionId.value)
    await Promise.all([loadSessions(), loadMessages()])
    await scrollToBottom()
  }
}

/**
 * 确认卡片上的「确认 / 取消」：调后端唤醒回合，并把卡片切成已完成状态。
 *
 * `remember` 只对"确认"有意义（勾了才记住）；取消一律按"本回合不再问"处理，
 * 由后端负责，前端不额外传。
 */
async function decideApproval(step: Step, approved: boolean, remember: boolean) {
  if (!step.tool_call_id) return
  step.status = 'sending'
  // 后端一旦卡住（最典型的是 --reload 撞上挂起的 SSE，进程卡在
  // "Waiting for connections to close"），这个 POST 会**永远不返回**，
  // 卡片就停在"处理中…"，用户完全不知道发生了什么。
  // 加个 15 秒超时，至少让他看到"后端可能在重启"。
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 15_000)
  try {
    const response = await fetch(`/api/v1/tool-calls/${step.tool_call_id}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved, remember }),
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const body = (await response.json()) as { waiting?: boolean }
    if (body.waiting === false) {
      // 后端已经没有在等这条了（超时、或后端重启过）——别假装确认成功
      throw new Error('后端已不再等待这条确认（可能已超时或重启过），请重新发起这轮对话')
    }
    step.status = approved ? 'approved' : 'cancelled'
  } catch (exc) {
    step.status = 'error'
    error.value = describeError(exc)
  } finally {
    window.clearTimeout(timer)
  }
  await scrollToBottom(true)
}

async function upload(event: Event) {
  const inputEl = event.target as HTMLInputElement
  const file = inputEl.files?.[0]
  if (!file || sessionId.value === null) return
  uploading.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('file', file)
    attachment.value = await api.post<AttachmentRow>(`/sessions/${sessionId.value}/attachments`, form)
    sessionCache.delete(sessionId.value)
    await loadMessages()
  } catch (exc) {
    error.value = describeError(exc)
  } finally {
    uploading.value = false
    inputEl.value = ''
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

watch(
  () => props.id,
  (value) => {
    const next = value ? Number(value) : null
    if (next !== sessionId.value) void openTarget(next)
  },
)

onMounted(async () => {
  await loadAgents()
  await openTarget(props.id ? Number(props.id) : null)
})
</script>

<template>
  <div class="chat-shell">
    <aside class="chat-side">
      <div style="padding: 14px 14px 10px">
        <button class="btn" style="width: 100%; justify-content: center" @click="newSession">
          ＋ 新建对话
        </button>
      </div>
      <div style="padding: 0 14px 10px">
        <input v-model="search" placeholder="搜索会话" />
      </div>
      <div class="chat-side__list">
        <div v-for="group in grouped" :key="group.key">
          <div class="eyebrow" style="padding: 8px 16px 4px">{{ group.key }}</div>
          <div
            v-for="item in group.items"
            :key="item.id"
            class="chat-side__item"
            :class="{ 'is-active': item.id === sessionId }"
            @click="openTarget(item.id)"
          >
            <div class="chat-side__title">{{ item.title }}</div>
            <div class="chat-side__meta">
              <span class="mono">#{{ item.id }}</span>
              <span class="spacer" />
              <button class="chat-side__del" title="删除会话" @click.stop="removeSession(item.id)">
                ✕
              </button>
            </div>
          </div>
        </div>
        <div v-if="!grouped.length" class="empty" style="padding: 20px 12px">
          还没有会话，点上面新建一个。
        </div>
      </div>
      <div class="chat-side__foot">
        <div class="row" style="gap: 8px">
          <span class="status-dot" />
          <span class="mono" style="font-size: 10px; letter-spacing: 0.14em; color: var(--ink-3)">
            REACT · 已连接
          </span>
          <span class="spacer" />
          <span class="mono" style="font-size: 10px; color: var(--ink-3)">
            {{ sessions.length }} 个会话
          </span>
        </div>
      </div>
    </aside>

    <main class="chat-main">
      <header class="chat-head">
        <div style="min-width: 0; flex: 1">
          <div class="row" style="gap: 8px">
            <span
              style="
                font-family: var(--font-serif);
                font-size: 16px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
              "
            >
              {{ session?.title || '会话' }}
            </span>
            <button class="btn btn--ghost btn--sm" @click="renameSession">改名</button>
          </div>
          <div class="row" style="gap: 8px; margin-top: 4px">
            <span class="eyebrow">Agent</span>
            <select
              style="width: 190px; padding: 3px 8px; font-size: 12px"
              :value="currentAgentId ?? ''"
              @change="switchAgent"
            >
              <option value="">— 未选择 —</option>
              <option v-for="item in agents" :key="item.id" :value="item.id">
                {{ item.name }}（{{ (item.tool_ids || []).length }} 个工具）
              </option>
            </select>
             <span v-if="notes.length" class="pill pill--ok">
               任务笔记 ×{{ notes.length }} · 窗口 W{{ notes[notes.length - 1].window_index }}
             </span>
          </div>
        </div>
      </header>

      <div class="chat-body">
        <p v-if="error" class="notice notice--error" style="margin-bottom: 12px; white-space: pre-wrap">
          {{ error }}
        </p>
        <p v-if="loading && visibleMessages.length" class="hint" style="text-align: center">
          正在加载会话…
        </p>

         <!-- 当前任务状态（D48）：换页后模型唯一的依据，摊开给用户看 -->
         <RunNoteCard v-if="notes.length" :notes="notes" />

        <div v-if="sessionId === null" class="chat-empty">
          <div class="seal" style="transform: rotate(-6deg)">诊</div>
          <h2 style="font-size: 22px; margin-top: 18px">还没有打开会话</h2>
          <p style="max-width: 44ch; margin: 8px auto 0; color: var(--ink-2); font-size: 13px">
            点左侧的「＋ 新建对话」开始一次新对话；或者从下面的历史里挑一个继续。
          </p>
        </div>
        <div v-else-if="!visibleMessages.length && !legacyReports.length" class="chat-empty">
          <div class="seal" style="transform: rotate(-6deg)">诊</div>
          <h2 style="font-size: 22px; margin-top: 18px">今天想让 Agent 做点什么？</h2>
          <p style="max-width: 48ch; margin: 8px auto 0; color: var(--ink-2); font-size: 13px">
            选一个 Agent，然后说人话就行：它会自己决定读简历、查真实 JD、请面试官质疑、再给终裁结论。
            思考与工具调用可以展开查看。
          </p>
          <div class="chat-suggest">
            <button
              v-for="text in [
                '帮我诊断这份简历，目标大数据开发实习生（附简历）',
                'Java 后端实习面试高频考点有哪些',
                '第 2 条致命弱点具体怎么改？',
                '给我一份 12 周冲刺路线图',
              ]"
              :key="text"
              @click="input = text"
            >
              {{ text }}
            </button>
          </div>
        </div>

        <div v-for="message in visibleMessages" :key="message.id">
          <div v-if="message.type === 'file'" class="bubble-row bubble-row--user">
            <span class="pill pill--seal">
              📎 {{ (message.content as any).filename }} ·
              {{ ((message.content as any).size / 1024).toFixed(0) }}KB
            </span>
          </div>

          <div v-else-if="message.role === 'user'" class="bubble-row bubble-row--user">
            <div class="bubble bubble--user">{{ textOf(message) }}</div>
          </div>

          <div v-else-if="kindOf(message) === 'ask'" class="bubble-row">
            <div class="avatar">问</div>
            <div style="min-width: 0; flex: 1">
              <div class="row" style="gap: 8px; margin-bottom: 6px">
                <span class="eyebrow">{{ agentNameOf(message) || '助手' }} · 需要你补充</span>
              </div>
              <div class="bubble bubble--ai bubble--ask">
                <MarkdownBlock :text="textOf(message)" :cache-key="message.id" />
              </div>
            </div>
          </div>

          <div v-else class="bubble-row">
            <div class="avatar">诊</div>
            <div style="min-width: 0; flex: 1">
              <div class="row" style="gap: 8px; margin-bottom: 6px">
                <span class="eyebrow">{{ agentNameOf(message) || '助手' }}</span>
              </div>
              <!-- 页面只保留这一种展示形态：不管这一轮有没有调工具，都先出现链路卡片 -->
              <StepsTimeline :steps="stepsOf(message)" empty-text="本轮未调用工具，直接作答" />
              <div class="bubble bubble--ai bubble--md">
                <MarkdownBlock :text="textOf(message)" :cache-key="message.id" />
              </div>
            </div>
          </div>
        </div>

        <ReportTextView
          v-for="item in legacyReports"
          :key="`report-${item.id}`"
          :report="item.content as unknown as Report"
          :message-id="item.id"
        />

        <!-- 流式：思考/行动/观察实时追加 -->
        <div v-if="sending" class="bubble-row">
          <div class="avatar">诊</div>
          <div style="min-width: 0; flex: 1">
            <div class="row" style="gap: 8px; margin-bottom: 6px">
              <span class="eyebrow">{{ liveStage }} · 已用 {{ elapsed }}s</span>
            </div>
            <StepsTimeline
              :steps="liveSteps"
              :default-open="true"
              streaming
              @decide="decideApproval"
            />
            <!-- 中间态不展示：流式 token 只在结束后作为最终回答出现 -->
            <div v-if="!liveSteps.length" class="bubble bubble--ai bubble--thinking">
              <span class="dot" /><span class="dot" style="animation-delay: 0.15s" /><span
                class="dot"
                style="animation-delay: 0.3s"
              />
            </div>
          </div>
        </div>

        <div ref="scrollAnchor" />
      </div>

      <footer class="chat-foot">
        <div class="composer">
          <textarea
            v-model="input"
            rows="1"
            :disabled="sessionId === null"
            :placeholder="
              sessionId === null
                ? '先点左侧「＋ 新建对话」开始'
                : '说点什么…… Enter 发送，Shift+Enter 换行'
            "
            @keydown="onKeydown"
            @input="
              ($event.target as HTMLTextAreaElement).style.height = 'auto';
              ($event.target as HTMLTextAreaElement).style.height =
                Math.min(($event.target as HTMLTextAreaElement).scrollHeight, 96) + 'px'
            "
          />
          <div class="composer__bar">
            <label
              class="btn btn--ghost btn--sm"
              :style="sessionId === null ? 'opacity: .5; pointer-events: none' : 'cursor: pointer'"
            >
              {{ uploading ? '上传中…' : '📎 附件' }}
              <input
                type="file"
                accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
                style="display: none"
                @change="upload"
              />
            </label>
            <span v-if="attachment" class="mono" style="font-size: 11px; color: var(--ink-3)">
              {{ attachment.filename }}
            </span>
            <span class="spacer" />
            <button
              class="btn btn--seal btn--sm"
              :disabled="sending || sessionId === null"
              @click="send"
            >
              {{ sending ? '处理中…' : '发送' }}
            </button>
          </div>
        </div>
      </footer>
    </main>
  </div>
</template>
