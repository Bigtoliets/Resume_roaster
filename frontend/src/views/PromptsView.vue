<script setup lang="ts">
import { computed, onMounted, reactive, watch } from 'vue'

import { api } from '@/api/client'
import FormRow from '@/components/form/FormRow.vue'
import JsonField from '@/components/form/JsonField.vue'
import MessageList from '@/components/form/MessageList.vue'
import ResourceWorkbench from '@/components/ResourceWorkbench.vue'
import TestBench from '@/components/TestBench.vue'
import { describeError, useResource } from '@/composables/useResource'

interface Message {
  role: string
  content: string
  order?: number
}

const {
  items,
  filtered,
  draft,
  selectedId,
  isNew,
  loading,
  saving,
  message,
  error,
  search,
  load,
  createNew,
  select,
  duplicate,
  save,
  remove,
} = useResource<any>('prompts', () => ({
  name: '',
  description: '',
  messages: [
    { role: 'system', content: '你是一个严谨的助手。', order: 1 },
    { role: 'user', content: '{{input}}', order: 2 },
  ],
  params: { temperature: 0.7 },
  variables: ['input'],
  is_enabled: true,
}))

const test = reactive({ busy: false, output: '', ok: null as boolean | null, variables: '{}' })

const messages = computed<Message[]>(() => (draft.value.messages as Message[]) || [])

/** 变量从 messages 里自动推导，避免手填漏填 */
const detectedVariables = computed(() => {
  const found = new Set<string>()
  for (const item of messages.value) {
    for (const match of (item.content || '').matchAll(/\{\{\s*([a-zA-Z_][\w]*)\s*\}\}/g)) {
      found.add(match[1])
    }
  }
  return [...found]
})

/** 变量标签形如 {{resume_json}}；放在 script 里拼，避免模板插值里嵌套花括号 */
const variableLabels = computed(() => detectedVariables.value.map((name) => `{{${name}}}`))

watch(detectedVariables, (value) => {
  draft.value.variables = value
})

function param(key: string, fallback: string) {
  return computed({
    get: () => (draft.value.params?.[key] ?? '') as string | number,
    set: (value: string | number) => {
      const params = { ...(draft.value.params || {}) }
      if (value === '' || value === null) delete params[key]
      else params[key] = typeof value === 'string' ? value : value
      draft.value.params = params
    },
  })
}

const temperature = param('temperature', '0.7')
const topP = param('top_p', '')
const maxTokens = param('max_tokens', '')

async function runTest() {
  if (selectedId.value === null) return
  test.busy = true
  test.output = ''
  try {
    const variables = JSON.parse(test.variables || '{}')
    const result = await api.post<Record<string, unknown>>(`/prompts/${selectedId.value}/test`, {
      variables,
      run: true,
    })
    test.ok = !result.error
    test.output = JSON.stringify(result, null, 2)
  } catch (exc) {
    test.ok = false
    test.output = describeError(exc)
  } finally {
    test.busy = false
  }
}

onMounted(load)
</script>

<template>
  <ResourceWorkbench
    index="02"
    title="Prompts"
    subtitle="messages 列表 + 模型参数 + 变量；渲染时未声明的变量会直接报错，不会静默留空"
    :items="filtered"
    :selected-id="selectedId"
    :draft-name="draft.name"
    :is-new="isNew"
    :is-loading="loading"
    :is-saving="saving"
    :message="message"
    :error="error"
    :search="search"
    @update:search="search = $event"
    @create="createNew"
    @select="select"
    @save="save()"
    @refresh="load"
    @remove="remove"
    @duplicate="duplicate"
  >
    <div class="grid grid--2">
      <FormRow label="名称" required>
        <input v-model="draft.name" placeholder="如 prompt_tech_lead_review" />
      </FormRow>
      <FormRow label="描述">
        <input v-model="draft.description" placeholder="这个 Prompt 给哪个节点用" />
      </FormRow>
    </div>

    <FormRow label="消息列表" hint="按 role 与顺序渲染；system 定义人设与铁律，user 是任务与变量">
      <MessageList v-model="draft.messages" />
    </FormRow>

    <div class="grid grid--2" style="margin-top: 6px">
      <FormRow label="temperature">
        <input type="number" step="0.1" min="0" max="2" v-model="temperature" />
      </FormRow>
      <FormRow label="top_p">
        <input type="number" step="0.05" min="0" max="1" v-model="topP" />
      </FormRow>
      <FormRow label="max_tokens">
        <input type="number" min="1" v-model="maxTokens" />
      </FormRow>
      <FormRow label="其他参数（JSON）" hint="上面三个之外的参数都写在这里">
        <JsonField v-model="draft.params" :rows="4" />
      </FormRow>
    </div>

    <FormRow label="变量" hint="由消息内容自动检测；这里只读展示，后端渲染时按此校验">
      <div class="chips">
        <span v-for="label in variableLabels" :key="label" class="chip is-on">{{ label }}</span>
        <span v-if="!detectedVariables.length" class="hint">消息里没有使用变量</span>
      </div>
    </FormRow>

    <template #test>
      <TestBench
        :busy="test.busy"
        :output="test.output"
        :ok="test.ok"
        run-label="渲染并试跑"
        @run="runTest"
      >
        <FormRow label="变量取值（JSON）" hint="用来渲染 messages；留空则渲染出未替换的占位符">
          <JsonField v-model="test.variables" :rows="4" />
        </FormRow>
      </TestBench>
    </template>
  </ResourceWorkbench>
</template>
