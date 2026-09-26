<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Report } from '@/api/types'
import LangfuseBadge from '@/components/LangfuseBadge.vue'

const props = defineProps<{ report: Report; messageId?: number }>()

const copied = ref(false)
const assemblyOpen = ref(false)

const assembly = computed(() => props.report.meta?.assembly || [])
const retrieval = computed(() => props.report.meta?.retrieval || [])
const list = (items?: string[]) => (items && items.length ? items.join('、') : '—')

const text = computed(() => (props.report.text || '').trim())
const legacy = computed(() => !text.value)

const filename = computed(() => {
  const role = props.report.meta?.target_role || '报告'
  const suffix = props.messageId ? `_${props.messageId}` : ''
  return `简历诊断报告_${role}${suffix}.txt`
})

async function copyAll() {
  if (!text.value) return
  try {
    await navigator.clipboard.writeText(text.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 1800)
  } catch {
    copied.value = false
  }
}

function download() {
  if (!text.value) return
  // 加 BOM：Windows 记事本打开时中文不乱码
  const blob = new Blob(['\ufeff' + text.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename.value
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <article class="card">
    <header class="card__head" style="align-items: center">
      <div>
        <span class="card__title">诊断报告 · 纯文本</span>
        <div class="eyebrow" style="margin-top: 3px">
          {{ report.meta?.target_role || '目标岗位' }} ｜
          {{ ((report.meta?.elapsed_ms || 0) / 1000).toFixed(1) }}s ｜
          {{ report.debate_summary?.rounds || 0 }} 轮 ｜ {{ text.length }} 字
        </div>
      </div>
      <div class="row" style="gap: 8px">
        <LangfuseBadge
          :url="report.meta?.langfuse_url"
          :trace-id="report.meta?.langfuse_trace_id"
        />
        <button class="btn btn--ghost btn--sm" type="button" :disabled="legacy" @click="copyAll">
          {{ copied ? '已复制' : '复制全文' }}
        </button>
        <button class="btn btn--sm" type="button" :disabled="legacy" @click="download">
          下载 .txt
        </button>
      </div>
    </header>

    <div class="card__body" style="padding: 0">
      <pre v-if="!legacy" class="report-text">{{ text }}</pre>
      <div v-else class="notice" style="margin: 18px">
        该报告由旧版本生成，不含纯文本产物。请重新跑一次诊断（约 2 分钟）即可获得纯文本报告。
      </div>
    </div>

    <div v-if="assembly.length" style="border-top: 1px solid var(--rule)">
      <div
        class="card__head"
        style="cursor: pointer"
        @click="assemblyOpen = !assemblyOpen"
      >
        <span class="card__title">本次装配</span>
        <span class="row" style="gap: 10px">
          <span class="eyebrow">{{ assembly.length }} 个节点 · 页面上配什么，这里就显示什么</span>
          <span class="mono">{{ assemblyOpen ? '收起 ▲' : '展开 ▼' }}</span>
        </span>
      </div>
      <div v-if="assemblyOpen" class="card__body">
        <table>
          <thead>
            <tr>
              <th style="width: 150px">节点</th>
              <th>Agent / Prompt</th>
              <th>模型</th>
              <th>Skills</th>
              <th>语料库</th>
              <th>工具</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in assembly" :key="item.node">
              <td class="mono">{{ item.node }}</td>
              <td>
                {{ item.agent || '—' }}
                <div class="hint">{{ item.prompt || '—' }}</div>
              </td>
              <td class="mono" style="font-size: 11px">{{ item.model || '—' }}</td>
              <td style="font-size: 12px">{{ list(item.skills) }}</td>
              <td style="font-size: 12px">{{ list(item.knowledge_bases) }}</td>
              <td style="font-size: 12px">{{ list(item.tools) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="retrieval.length" style="margin-top: 12px">
          <div class="eyebrow" style="margin-bottom: 6px">本次检索</div>
          <div v-for="item in retrieval" :key="item.collection + item.knowledge_base" class="hint">
            {{ item.knowledge_base }}（{{ item.collection }}，top{{ item.top_k
            }}{{ item.id_prefix ? '，前缀 ' + item.id_prefix : '' }}）→ 命中 {{ item.count }} 条 ·
            {{ item.elapsed_ms }}ms
          </div>
        </div>
      </div>
    </div>
  </article>
</template>
