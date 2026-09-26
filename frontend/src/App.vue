<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import DialogHost from '@/components/DialogHost.vue'
import StatusPill from '@/components/StatusPill.vue'
import { navigating } from '@/composables/useNavProgress'

const route = useRoute()
const plain = computed(() => Boolean(route.meta.plain))

interface NodeLink {
  to: string
  label: string
  index: string
}

const buildGroup: NodeLink[] = [
  { to: '/models', label: '模型配置', index: '01' },
  { to: '/prompts', label: 'Prompts', index: '02' },
  { to: '/tools', label: 'Tools', index: '03' },
  { to: '/knowledge-bases', label: '知识库', index: '04' },
  { to: '/skills', label: 'Skills', index: '05' },
  { to: '/agents', label: 'Agents', index: '06' },
]

const runGroup: NodeLink[] = [
  { to: '/', label: '总览', index: '00' },
  { to: '/sessions', label: '会话', index: '07' },
]

const status = ref<Record<string, any> | null>(null)
const statusError = ref('')

const pageTitle = computed(() => {
  const map: Record<string, string> = {
    overview: '总览',
    models: '模型配置',
    prompts: 'Prompts',
    tools: 'Tools',
    'knowledge-bases': '知识库',
    skills: 'Skills',
    agents: 'Agents',
    sessions: '会话',
    'session-detail': '诊断会话',
  }
  return map[String(route.name ?? '')] ?? '简历诊断平台'
})

function dot(key: string) {
  const entry = status.value?.[key]
  if (!entry?.configured) return 'idle'
  if (entry.ok === false || entry.error) return 'warn'
  return 'ok'
}

onMounted(async () => {
  if (plain.value) return
  try {
    status.value = await api.get('/system/status')
  } catch (exc) {
    statusError.value = String(exc)
  }
})
</script>

<template>
  <div v-if="plain">
    <router-view />
  </div>

  <div v-else class="shell">
    <aside class="rail">
      <div class="brand">
        <div class="brand__mark">简历诊断<br />卷宗室</div>
        <div class="brand__sub">Resume Roaster</div>
      </div>

      <div class="rail__group">运行验证</div>
      <router-link v-for="link in runGroup" :key="link.to" class="rail__link" :to="link.to">
        <span class="rail__index">{{ link.index }}</span>
        <span>{{ link.label }}</span>
      </router-link>

      <div class="rail__group">Agent 构建</div>
      <router-link v-for="link in buildGroup" :key="link.to" class="rail__link" :to="link.to">
        <span class="rail__index">{{ link.index }}</span>
        <span>{{ link.label }}</span>
      </router-link>
    </aside>

    <div class="main">
      <!-- 路由切换进度条 -->
      <div class="nav-progress" :class="{ 'is-on': navigating }" aria-hidden="true" />
      <header class="topbar">
        <div>
          <div class="topbar__title">{{ pageTitle }}</div>
          <div class="topbar__meta">ReAct · 单主节点 + 工具</div>
        </div>
        <div class="row" style="gap: 8px">
          <StatusPill
            :label="`MySQL ${status?.mysql?.ok ? 'OK' : status?.mysql?.configured ? 'ERR' : 'OFF'}`"
            :state="dot('mysql')"
            :title="status?.mysql?.error || '平台配置与会话存储'"
          />
          <StatusPill
            :label="`Milvus ${status?.milvus?.version || (status?.milvus?.configured ? '…' : 'OFF')}`"
            :state="dot('milvus')"
            :title="status?.milvus?.error || status?.milvus?.warning || '向量检索'"
          />
          <StatusPill
            :label="`Langfuse ${status?.langfuse?.configured ? 'ON' : 'OFF'}`"
            :state="status?.langfuse?.configured ? 'ok' : 'idle'"
            :title="status?.langfuse?.url || '未配置'"
          />
          <StatusPill
            :label="`Embedding ${status?.embedding?.provider || '—'}`"
            :state="status?.embedding?.provider === 'local' ? 'ok' : 'warn'"
            :title="`${status?.embedding?.model || ''} · ${status?.embedding?.dim || ''} 维`"
          />
        </div>
      </header>

      <main class="page">
        <p v-if="statusError" class="notice notice--error" style="margin-bottom: 16px">
          后端未连接：{{ statusError }}。请确认已启动 <code>uvicorn app.main:app</code>。
        </p>
        <router-view />
      </main>
    </div>

    <!-- 全局对话框：确认删除 / 重命名等，替代原生 confirm / prompt -->
    <DialogHost />
  </div>
</template>
