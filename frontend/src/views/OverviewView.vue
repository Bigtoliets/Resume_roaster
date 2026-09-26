<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'

const status = ref<Record<string, any> | null>(null)
const error = ref('')

const entries = [
  { to: '/sessions', title: '开始一次诊断', desc: '上传简历 PDF → 填目标岗位 → 看红蓝对抗与报告' },
  { to: '/agents', title: '配置面试官', desc: '模型、Prompt、Skills、知识库的组合与版本快照' },
  { to: '/knowledge-bases', title: '接入语料', desc: '4 字段 collection 的连接配置与混合检索测试' },
]

onMounted(async () => {
  try {
    status.value = await api.get('/system/status')
  } catch (exc) {
    error.value = String(exc)
  }
})
</script>

<template>
  <section>
    <header class="rise" style="max-width: 78ch; margin-bottom: 26px">
      <div class="eyebrow">Scene · resume_diagnosis</div>
      <h1 style="font-size: 32px; margin: 8px 0 10px; line-height: 1.25">
        让技术面试官和 HR 当面对质，<br />再给你一份能直接改简历的判决书
      </h1>
      <p style="color: var(--ink-2); font-family: var(--font-serif); font-size: 15px; margin: 0">
        上传简历、填写目标岗位，系统检索真实招聘语料，由两个不同人设的面试官多轮互相反驳，
        Supervisor 审核必查项覆盖率后收敛，输出致命弱点、逐条改写建议与 12 周冲刺路线图。
      </p>
    </header>

    <div class="grid grid--2" style="margin-bottom: 26px">
      <RouterLink
        v-for="(entry, index) in entries"
        :key="entry.to"
        :to="entry.to"
        class="card rise"
        :class="`rise-${index + 1}`"
        style="padding: 18px; display: block; transition: transform .18s ease"
      >
        <div class="eyebrow">入口 {{ String(index + 1).padStart(2, '0') }}</div>
        <div style="font-family: var(--font-serif); font-size: 18px; margin: 6px 0 4px">
          {{ entry.title }}
        </div>
        <div style="color: var(--ink-2); font-size: 13px">{{ entry.desc }}</div>
      </RouterLink>
    </div>

    <div class="card rise rise-4">
      <div class="card__head">
        <span class="card__title">运行环境</span>
        <span class="eyebrow">/api/v1/system/status</span>
      </div>
      <div class="card__body">
        <p v-if="error" class="notice notice--error">后端未连接：{{ error }}</p>
        <div v-else-if="status" class="grid grid--2">
          <div>
            <div class="eyebrow">MySQL</div>
            <div class="mono">{{ status.mysql?.ok ? '连接正常' : status.mysql?.error || '未配置' }}</div>
          </div>
          <div>
            <div class="eyebrow">Milvus</div>
            <div class="mono">
              {{ status.milvus?.version || '未配置' }}
              <span v-if="status.milvus?.warning" style="color: var(--warn)"> · {{ status.milvus.warning }}</span>
            </div>
          </div>
          <div>
            <div class="eyebrow">Embedding</div>
            <div class="mono">
              {{ status.embedding?.provider }} · {{ status.embedding?.model }} · {{ status.embedding?.dim }} 维
            </div>
          </div>
          <div>
            <div class="eyebrow">Langfuse</div>
            <div class="mono">{{ status.langfuse?.configured ? status.langfuse?.url : '未配置（trace 图标将置灰）' }}</div>
          </div>
        </div>
        <div v-if="status?.builtin_tools?.length" class="row" style="margin-top: 14px">
          <span class="eyebrow">内置工具</span>
          <span v-for="tool in status.builtin_tools" :key="tool" class="pill">{{ tool }}</span>
        </div>
      </div>
    </div>
  </section>
</template>
