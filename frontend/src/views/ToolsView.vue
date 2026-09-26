<script setup lang="ts">
/**
 * 工具：页面只认 JSON，不认任何具体工具（D32.10）。
 *
 * 页面上的三个 JSON 框就是权威：
 *   入参 JSON —— 给模型看的参数结构（模型照它传参）
 *   出参 JSON —— 结果长什么样，存档 + 展示
 *   配置 JSON —— 运行时配置（知识库 id、URL 等），执行时作为 input.config 交给函数
 * 实现（代码）只负责在新建时带出一份默认值，之后页面想怎么改就怎么改。
 */
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '@/api/client'
import FormRow from '@/components/form/FormRow.vue'
import JsonField from '@/components/form/JsonField.vue'
import RefSelect from '@/components/form/RefSelect.vue'
import ResourceWorkbench from '@/components/ResourceWorkbench.vue'
import TestBench from '@/components/TestBench.vue'
import { describeError, useResource } from '@/composables/useResource'
import { cloneDeep } from '@/utils/clone'

interface Impl {
  impl_key: string
  title: string
  description: string
  kind: 'llm' | 'builtin'
  needs_prompt: boolean
  params_schema: Record<string, unknown>
  returns_schema: Record<string, unknown>
  config: Record<string, unknown>
  default_timeout_s: number
}

interface TestCase {
  name: string
  args: Record<string, unknown>
  config: Record<string, unknown>
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
} = useResource<any>('tools', () => ({
  name: '',
  display_name: '',
  description: '',
  impl_key: '',
  prompt_id: null,
  model_config_id: null,
  requires_approval: false,
  timeout_s: 120,
  retry_times: 1,
  params_schema: { type: 'object', properties: {}, required: [] },
  returns_schema: { type: 'object' },
  impl_config: {},
  test_cases: [],
  is_enabled: true,
}))

const impls = ref<Impl[]>([])
const implsLoaded = ref(false)
const test = reactive({
  busy: false,
  args: {} as Record<string, unknown>,
  config: {} as Record<string, unknown>,
  output: '',
  ok: null as boolean | null,
  elapsed: 0,
})

const activeImpl = computed(() => impls.value.find((item) => item.impl_key === draft.value.impl_key))
const status = computed(() => {
  if (!draft.value.impl_key) return 'unset'
  if (!implsLoaded.value) return 'loading'
  return activeImpl.value ? 'ok' : 'missing'
})
const statusLabel = computed(
  () =>
    ({ ok: '已实现', missing: '未实现', unset: '未指定实现', loading: '检查中…' })[status.value],
)
const testCases = computed<TestCase[]>(() => draft.value.test_cases || [])

/** 选实现 = 把这套实现的默认值带进表单；之后随便改，代码不再干预。 */
function onImplChange() {
  const impl = activeImpl.value
  if (!impl) return
  draft.value.params_schema = cloneDeep(impl.params_schema)
  draft.value.returns_schema = cloneDeep(impl.returns_schema)
    draft.value.impl_config = cloneDeep(impl.config)
    draft.value.timeout_s = impl.default_timeout_s
    draft.value.retry_times = 1
}

function resetTestToDraft() {
  const required = (draft.value.params_schema?.required || []) as string[]
  const properties = (draft.value.params_schema?.properties || {}) as Record<
    string,
    { type?: string; default?: unknown }
  >
  const skeleton: Record<string, unknown> = {}
  for (const key of required) {
    const meta = properties[key] || {}
    skeleton[key] =
      meta.default ??
      (meta.type === 'integer' || meta.type === 'number'
        ? 0
        : meta.type === 'boolean'
          ? false
          : meta.type === 'array'
            ? []
            : '')
  }
  test.args = skeleton
  test.config = cloneDeep(draft.value.impl_config || {})
}

async function runTest() {
  test.busy = true
  test.output = ''
  try {
    const result = await api.post<{
      ok: boolean
      output: Record<string, unknown>
      elapsed_ms: number
    }>('/tools/test', {
      impl_key: draft.value.impl_key,
      args: test.args || {},
      config: test.config || {},
      prompt_id: draft.value.prompt_id,
      model_config_id: draft.value.model_config_id,
      timeout_s: draft.value.timeout_s,
      name: draft.value.name || 'preview_tool',
      display_name: draft.value.display_name || '',
    })
    test.ok = result.ok
    test.elapsed = result.elapsed_ms
    test.output = JSON.stringify(result.output, null, 2)
  } catch (exc) {
    test.ok = false
    test.output = describeError(exc)
  } finally {
    test.busy = false
  }
}

function saveCase() {
  const cases = cloneDeep(testCases.value)
  cases.push({
    name: `用例 ${cases.length + 1}`,
    args: cloneDeep(test.args || {}),
    config: cloneDeep(test.config || {}),
  })
  draft.value.test_cases = cases
}

function loadCase(item: TestCase) {
  test.args = cloneDeep(item.args || {})
  test.config = cloneDeep(item.config || {})
}

function dropCase(index: number) {
  const cases = cloneDeep(testCases.value)
  cases.splice(index, 1)
  draft.value.test_cases = cases
}

onMounted(async () => {
  await load()
  try {
    const data = await api.get<{ impls: Impl[] }>('/tools/impls')
    impls.value = data.impls || []
  } catch {
    impls.value = []
  } finally {
    implsLoaded.value = true
  }
})
</script>

<template>
  <ResourceWorkbench
    index="03"
    title="Tools"
    subtitle="页面注册 + 代码实现：三个 JSON 框决定模型看到什么、运行时拿到什么；代码只负责执行"
    :items="filtered"
    :selected-id="selectedId"
    :draft-name="draft.display_name || draft.name"
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
      <FormRow label="工具标识" required hint="英文名，会作为函数名给模型看，如 kb_search_perf">
        <input v-model="draft.name" placeholder="kb_search_perf" />
      </FormRow>
      <FormRow label="显示名" hint="会话里展示给用户看，如「招聘语料检索」">
        <input v-model="draft.display_name" placeholder="招聘语料检索" />
      </FormRow>
    </div>

    <FormRow
      label="实现（来自代码）"
      hint="选一个 app/tools/ 下已注册的实现；选中后会带出它的默认 JSON，之后随便改"
    >
      <div class="row" style="gap: 8px; align-items: center">
        <select v-model="draft.impl_key" style="flex: 1" @change="onImplChange">
          <option value="">— 未指定 —</option>
          <option v-for="impl in impls" :key="impl.impl_key" :value="impl.impl_key">
            {{ impl.title }}（{{ impl.impl_key }}）
          </option>
        </select>
        <span class="pill" :class="{ 'pill--warn': status !== 'ok' }">{{ statusLabel }}</span>
      </div>
      <div v-if="activeImpl" class="hint">{{ activeImpl.description }}</div>
      <div v-else-if="draft.impl_key" class="hint" style="color: var(--seal)">
        后端没有 impl_key 为「{{ draft.impl_key }}」的实现，调用时会返回「未实现」而不是崩溃。
      </div>
    </FormRow>

    <FormRow label="说明（给模型看）" hint="留空则用代码里的说明；写得越清楚，ReAct 选工具越准">
      <textarea rows="3" spellcheck="false" v-model="draft.description" />
    </FormRow>

    <template v-if="activeImpl?.needs_prompt">
      <div class="grid grid--2">
        <FormRow label="Prompt" required hint="必选：从 Prompts 库选一条，它就是这工具的角色与任务">
          <RefSelect slug="prompts" v-model="draft.prompt_id" empty-label="— 请选择 Prompt —" />
        </FormRow>
        <FormRow label="模型" hint="留空则沿用调用它的 Agent 的模型">
          <RefSelect
            slug="models"
            v-model="draft.model_config_id"
            empty-label="— 沿用 Agent 模型 —"
          />
        </FormRow>
      </div>
    </template>

    <div class="grid grid--2">
      <FormRow label="入参 JSON" hint="给模型看的参数结构；模型照它传参，执行时原样交给代码">
        <JsonField v-model="draft.params_schema" :rows="10" />
      </FormRow>
      <FormRow label="出参 JSON" hint="结果长什么样（存档与展示用，不影响执行）">
        <JsonField v-model="draft.returns_schema" :rows="10" />
      </FormRow>
    </div>

    <FormRow
      label="配置 JSON"
      hint="执行时作为 input[&quot;config&quot;] 交给代码：知识库 id、URL、前缀等固定配置都放这里"
    >
      <JsonField v-model="draft.impl_config" :rows="8" />
    </FormRow>

    <div class="grid grid--2">
      <FormRow
        label="工具内超时（秒）"
        hint="单个工具跑过这个时间就中断，返回一条超时观察结果，不会拖着整个回合"
      >
        <input type="number" v-model.number="draft.timeout_s" />
      </FormRow>
      <FormRow
        label="失败重试次数"
        hint="只对超时 / 限流 / 服务端错误重试；参数错误不重试。0 = 不重试（最多 3）"
      >
        <input type="number" v-model.number="draft.retry_times" min="0" max="3" />
      </FormRow>
      <FormRow label="人工确认" hint="开启后，模型每次调用它都会先弹确认框等你批准">
        <label class="row" style="gap: 6px">
          <input type="checkbox" style="width: auto" v-model="draft.requires_approval" />
          <span>{{ draft.requires_approval ? '需要我确认后才执行' : '直接执行' }}</span>
        </label>
      </FormRow>
      <FormRow label="启用">
        <label class="row" style="gap: 6px">
          <input type="checkbox" style="width: auto" v-model="draft.is_enabled" />
          <span>{{ draft.is_enabled ? '启用' : '停用' }}</span>
        </label>
      </FormRow>
    </div>

    <template #test>
      <TestBench
        :busy="test.busy"
        :output="test.output"
        :ok="test.ok"
        run-label="执行（不落库）"
        @run="runTest"
      >
        <div class="row" style="justify-content: space-between; margin-bottom: 4px">
          <span class="eyebrow">入参 JSON</span>
          <div class="row" style="gap: 6px">
            <button class="btn btn--ghost btn--sm" type="button" @click="resetTestToDraft">
              按表单生成骨架
            </button>
            <button class="btn btn--ghost btn--sm" type="button" @click="saveCase">
              存成用例
            </button>
          </div>
        </div>
        <JsonField v-model="test.args" :rows="6" />

        <div class="eyebrow" style="margin: 12px 0 4px">配置 JSON（可临时改，不影响已保存的值）</div>
        <JsonField v-model="test.config" :rows="5" />

        <ul v-if="testCases.length" class="cases">
          <li v-for="(item, index) in testCases" :key="index">
            <button class="cases__load" type="button" @click="loadCase(item)">
              {{ item.name }}
            </button>
            <button class="cases__drop" type="button" @click="dropCase(index)">删除</button>
          </li>
        </ul>
        <p class="hint" style="margin-top: 6px">
          用例存在工具的 test_cases 字段里，保存后长期可用。
        </p>
      </TestBench>
    </template>
  </ResourceWorkbench>
</template>

<style scoped>
.cases {
  list-style: none;
  padding: 0;
  margin: 10px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cases li {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--rule-strong);
  border-radius: 999px;
  padding: 2px 6px 2px 10px;
  background: #fbf9f4;
}

.cases__load,
.cases__drop {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--ink-2);
  padding: 2px;
}

.cases__drop {
  color: var(--seal);
}
</style>
