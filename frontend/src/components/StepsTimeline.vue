<script setup lang="ts">
/** ReAct 步骤时间线：思考 / 行动 / 观察，默认折叠，展开时按需拉工具调用详情。 */
import { computed, ref } from 'vue'

import { api } from '@/api/client'

interface Step {
  type: 'thought' | 'action' | 'observation'
  text?: string
  tool?: string
  tool_display?: string
  arguments?: Record<string, unknown>
  tool_call_id?: number | null
  summary?: string
  status?: string
  elapsed_ms?: number
}

const props = defineProps<{ steps: Step[]; defaultOpen?: boolean; streaming?: boolean }>()

const open = ref(props.defaultOpen ?? false)
const details = ref<Record<number, string>>({})
const loadingId = ref<number | null>(null)

const counts = computed(() => ({
  thought: props.steps.filter((s) => s.type === 'thought').length,
  action: props.steps.filter((s) => s.type === 'action').length,
}))

async function toggleDetail(step: Step) {
  if (!step.tool_call_id) return
  const id = step.tool_call_id
  if (details.value[id]) {
    const next = { ...details.value }
    delete next[id]
    details.value = next
    return
  }
  loadingId.value = id
  try {
    const data = await api.get<Record<string, unknown>>(`/tool-calls/${id}`)
    details.value = { ...details.value, [id]: JSON.stringify(data, null, 2) }
  } catch (exc) {
    details.value = { ...details.value, [id]: `加载失败：${exc}` }
  } finally {
    loadingId.value = null
  }
}

function argsPreview(step: Step): string {
  const args = step.arguments || {}
  const text = JSON.stringify(args)
  return text.length > 110 ? `${text.slice(0, 110)}…` : text
}
</script>

<template>
  <div v-if="steps.length" class="steps">
    <button class="steps__head" type="button" @click="open = !open">
      <span class="steps__badge">💭</span>
      <span class="steps__title">
        思考与行动 · {{ counts.thought }} 次思考 / {{ counts.action }} 次工具调用
      </span>
      <span v-if="streaming" class="pill pill--seal">进行中</span>
      <span class="spacer" />
      <span class="mono" style="color: var(--ink-3)">{{ open ? '收起 ▲' : '展开 ▼' }}</span>
    </button>

    <div v-if="open" class="steps__body">
      <div
        v-for="(step, index) in steps"
        :key="index"
        class="step"
        :class="`step--${step.type}`"
      >
        <span class="step__mark">
          {{ step.type === 'thought' ? '💭' : step.type === 'action' ? '🔧' : '👁' }}
        </span>
        <div style="min-width: 0; flex: 1">
          <template v-if="step.type === 'thought'">
            <div class="step__label">思考</div>
            <div class="step__thought">{{ step.text }}</div>
          </template>

          <template v-else-if="step.type === 'action'">
            <div class="row" style="gap: 8px">
              <span class="step__label">行动</span>
              <span class="step__tool">{{ step.tool_display || step.tool }}</span>
              <span class="mono" style="color: var(--ink-3); font-size: 10px">{{ step.tool }}</span>
            </div>
            <div class="step__args">{{ argsPreview(step) }}</div>
          </template>

          <template v-else>
            <div class="row" style="gap: 8px">
              <span class="step__label">观察</span>
              <span class="step__tool">{{ step.tool_display || step.tool }}</span>
              <span v-if="step.status === 'rejected'" class="pill pill--seal">已拒绝</span>
              <span v-else-if="step.status === 'error'" class="pill pill--warn">出错</span>
            </div>
            <div class="step__summary">{{ step.summary }}</div>
            <button
              v-if="step.tool_call_id"
              class="btn btn--ghost btn--sm"
              style="margin-top: 6px"
              type="button"
              :disabled="loadingId === step.tool_call_id"
              @click="toggleDetail(step)"
            >
              {{ details[step.tool_call_id!] ? '收起完整结果' : loadingId === step.tool_call_id ? '加载中…' : '查看完整参数与结果' }}
            </button>
            <pre v-if="details[step.tool_call_id!]" class="step__detail">{{ details[step.tool_call_id!] }}</pre>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.steps {
  border: 1px solid var(--rule);
  border-radius: 12px;
  background: var(--paper-2);
  overflow: hidden;
  margin-bottom: 8px;
}

.steps__head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.steps__head:hover {
  background: rgba(25, 23, 18, 0.03);
}

.steps__badge {
  font-size: 13px;
}

.steps__title {
  font-size: 12.5px;
  color: var(--ink-2);
}

.steps__body {
  border-top: 1px dashed var(--rule-strong);
  padding: 10px 12px;
  background: var(--paper);
  max-height: 420px;
  overflow-y: auto;
}

.step {
  display: flex;
  gap: 9px;
  padding: 8px 0;
  border-bottom: 1px solid var(--rule);
}

.step:last-child {
  border-bottom: none;
}

.step__mark {
  font-size: 12px;
  line-height: 1.6;
}

.step__label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.step__tool {
  font-size: 12.5px;
  color: var(--seal-ink);
}

.step__thought {
  font-family: var(--font-serif);
  font-size: 13px;
  color: var(--ink-2);
  font-style: italic;
  margin-top: 2px;
  white-space: pre-wrap;
}

.step__args,
.step__summary {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  margin-top: 3px;
  word-break: break-all;
}

.step__detail {
  margin: 8px 0 0;
  max-height: 320px;
  overflow: auto;
  border: 1px solid var(--rule);
  padding: 10px;
  background: #fbf9f4;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 11px;
  line-height: 1.7;
}
</style>
