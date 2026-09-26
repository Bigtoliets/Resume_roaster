<script setup lang="ts">
import { onMounted, reactive } from 'vue'

import { api } from '@/api/client'
import FormRow from '@/components/form/FormRow.vue'
import JsonField from '@/components/form/JsonField.vue'
import ResourceWorkbench from '@/components/ResourceWorkbench.vue'
import TestBench from '@/components/TestBench.vue'
import { describeError, useResource } from '@/composables/useResource'

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
} = useResource<any>('models', () => ({
  name: '',
  description: '',
  base_url: 'https://api.deepseek.com',
  api_key: '',
  model_name: 'deepseek-chat',
  default_params: { temperature: 0.7, max_tokens: 4096 },
  timeout_s: 120,
  is_default: false,
  is_enabled: true,
}))

const test = reactive({ busy: false, output: '', ok: null as boolean | null })

async function saveModel() {
  // API Key 只在填了的时候提交：留空 = 保持原密钥不变
  const payload: Record<string, unknown> = { ...draft.value }
  if (!payload.api_key) delete payload.api_key
  await save(payload)
}

async function runTest() {
  if (selectedId.value === null) return
  test.busy = true
  test.output = ''
  try {
    const result = await api.post<Record<string, unknown>>(`/models/${selectedId.value}/test`)
    test.ok = Boolean(result.ok)
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
    index="01"
    title="模型配置"
    subtitle="同一 OpenAI 兼容协议下的多套配置，可被不同 Agent 分别使用（例如辩论用 chat、终裁用 reasoner）"
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
    @save="saveModel"
    @refresh="load"
    @remove="remove"
    @duplicate="duplicate"
  >
    <div class="grid grid--2">
      <FormRow label="名称" required hint="模型配置的标识，也是 Agent 绑定时的显示名">
        <input v-model="draft.name" placeholder="如 deepseek-chat" />
      </FormRow>
      <FormRow label="模型名（请求参数）">
        <input v-model="draft.model_name" placeholder="如 deepseek-chat / deepseek-reasoner" />
      </FormRow>
      <FormRow label="Base URL">
        <input v-model="draft.base_url" placeholder="https://api.deepseek.com" />
      </FormRow>
      <FormRow
        label="API Key"
        :hint="isNew ? '以 Fernet 密文落库，列表只显示掩码' : `当前已配置，留空表示不修改（${draft.api_key_masked || '已隐藏'}）`"
      >
        <input type="password" v-model="draft.api_key" placeholder="sk-…" />
      </FormRow>
      <FormRow label="超时（秒）" hint="辩论类节点建议 120，推理类建议 240">
        <input type="number" v-model.number="draft.timeout_s" />
      </FormRow>
      <FormRow label="描述">
        <input v-model="draft.description" placeholder="这套配置准备给谁用" />
      </FormRow>
      <FormRow label="默认参数" hint="直接透传给模型的参数，如 temperature / max_tokens">
        <JsonField v-model="draft.default_params" :rows="6" />
      </FormRow>
      <FormRow label="开关">
        <div class="row" style="gap: 16px">
          <label class="row" style="gap: 6px">
            <input type="checkbox" style="width: auto" v-model="draft.is_default" />
            <span>设为默认</span>
          </label>
          <label class="row" style="gap: 6px">
            <input type="checkbox" style="width: auto" v-model="draft.is_enabled" />
            <span>启用</span>
          </label>
        </div>
      </FormRow>
    </div>

    <template #test>
      <TestBench
        :busy="test.busy"
        :output="test.output"
        :ok="test.ok"
        run-label="测试连通性"
        @run="runTest"
      >
        <p class="hint" style="margin: 0">
          会真实发一次最小请求（"回复 ok"），返回延迟与模型回显。保存后才能测试。
        </p>
      </TestBench>
    </template>
  </ResourceWorkbench>
</template>
