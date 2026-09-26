<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '@/api/client'
import FormRow from '@/components/form/FormRow.vue'
import JsonField from '@/components/form/JsonField.vue'
import ResourceWorkbench from '@/components/ResourceWorkbench.vue'
import TestBench from '@/components/TestBench.vue'
import { describeError, useResource } from '@/composables/useResource'

interface Hit {
  id: string
  score: number
  /** 后端 search_knowledge_bases 返回的正文片段字段名就是 text（不是 raw_text） */
  text: string
  knowledge_base?: string
  collection?: string
  /** 靠哪一路召回：dense（语义）/ bm25（关键词）/ both（两路都中） */
  retrieval?: string
  matched_queries?: string[]
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
} = useResource<any>('knowledge-bases', () => ({
  name: '',
  description: '',
  vector_store: 'milvus',
  collection: 'jd',
  embedding_model: 'text-embedding-v4',
  chunk_config: {},
  top_k: 10,
  rrf_k: 60,
  recall_multiplier: 3,
  snippet_chars: 1200,
  min_score: 0,
  filter_fields: { id_prefix: '' },
  is_enabled: true,
}))

// 一期只维护 jd；跑起来之后会被 /system/status 返回的真实 collection 列表覆盖
const collections = ref<string[]>(['jd'])
const test = reactive({
  busy: false,
  query: '',
  topK: 3,
  hits: [] as Hit[],
  ok: null as boolean | null,
  error: '',
})

const idPrefix = computed({
  get: () => (draft.value.filter_fields?.id_prefix ?? '') as string,
  set: (value: string) => {
    draft.value.filter_fields = { ...(draft.value.filter_fields || {}), id_prefix: value }
  },
})

const collectionOptions = computed(() =>
  collections.value.includes(draft.value.collection as string)
    ? collections.value
    : [...collections.value, String(draft.value.collection || '')].filter(Boolean),
)

async function runSearch() {
  if (selectedId.value === null || !test.query.trim()) return
  test.busy = true
  test.error = ''
  test.hits = []
  try {
    // 多条检索语句用 | 或换行分隔：一次覆盖多个角度，命中率更高
    const queries = test.query
      .split(/[|\n]/)
      .map((item) => item.trim())
      .filter(Boolean)
    const result = await api.post<{ hits: Hit[] }>(`/knowledge-bases/${selectedId.value}/search`, {
      queries,
      top_k: test.topK,
      id_prefix: idPrefix.value || undefined,
    })
    test.hits = result.hits || []
    test.ok = test.hits.length > 0
  } catch (exc) {
    test.ok = false
    test.error = describeError(exc)
  } finally {
    test.busy = false
  }
}

onMounted(async () => {
  await load()
  try {
    const status = await api.get<{ milvus: { collections?: string[] }; embedding: { model: string } }>(
      '/system/status',
    )
    if (status.milvus?.collections?.length) collections.value = status.milvus.collections
    if (status.embedding?.model && !draft.value.embedding_model) {
      draft.value.embedding_model = status.embedding.model
    }
  } catch {
    /* 状态接口不可用时保留默认值 */
  }
})
</script>

<template>
  <ResourceWorkbench
    index="04"
    title="知识库"
    subtitle="只做连接与检索参数配置：建表由系统负责，语料由你自己维护（一行 = 一篇文档）"
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
        <input v-model="draft.name" placeholder="如 JD 语料库" />
      </FormRow>
      <FormRow
        label="Collection"
        hint="系统负责建表；一行 = 一篇 JD，字段 id / content / dense / sparse(BM25 自动生成)"
      >
        <select v-model="draft.collection">
          <option v-for="name in collectionOptions" :key="name" :value="name">{{ name }}</option>
        </select>
      </FormRow>
      <FormRow label="Embedding 模型" hint="与 .env 中的 EMBEDDING_MODEL 保持一致">
        <input v-model="draft.embedding_model" />
      </FormRow>
      <FormRow label="默认 Top K" hint="辩论节点用的召回条数（JD 建议 10，面经建议 5）">
        <input type="number" v-model.number="draft.top_k" min="1" />
      </FormRow>
      <FormRow
        label="id 前缀过滤（可选）"
        hint="按岗位族收窄检索，如 bigdata_ / java_ / data_；留空则全库检索"
      >
        <input v-model="idPrefix" placeholder="bigdata_" />
      </FormRow>
    </div>

    <div class="card" style="background: var(--paper-2); border-style: dashed">
      <div class="card__body">
        <div class="eyebrow" style="margin-bottom: 8px">检索行为（D35：调到合适的召回与噪音平衡）</div>
        <div class="grid grid--2">
          <FormRow
            label="RRF 平滑常数 k"
            hint="默认 60。调小会让第一名更突出、靠后的名次更被压制；调大则更平缓"
          >
            <input type="number" v-model.number="draft.rrf_k" min="1" />
          </FormRow>
          <FormRow
            label="每路召回倍数"
            hint="默认 3：两路各先召回 top_k×3 条再融合。调大提高召回率，代价是更慢"
          >
            <input type="number" v-model.number="draft.recall_multiplier" min="1" />
          </FormRow>
          <FormRow label="返回片段长度（字符）" hint="默认 1200。调小能塞更多条，调大能让模型看全文">
            <input type="number" v-model.number="draft.snippet_chars" min="200" />
          </FormRow>
          <FormRow
            label="分数下限 min_score"
            hint="默认 0（不过滤）。RRF 分数里「两路都命中第 1」约 0.033，填 0.03 能滤掉单路弱命中"
          >
            <input type="number" step="0.001" v-model.number="draft.min_score" min="0" />
          </FormRow>
        </div>
      </div>
    </div>

    <div class="grid grid--2">
      <FormRow label="描述">
        <input v-model="draft.description" />
      </FormRow>
    </div>

    <div class="grid grid--2">
      <FormRow label="切分配置（JSON）" hint="4 字段设计默认一行一篇，无需切分；这里仅预留">
        <JsonField v-model="draft.chunk_config" :rows="4" />
      </FormRow>
      <FormRow label="开关">
        <label class="row" style="gap: 6px">
          <input type="checkbox" style="width: auto" v-model="draft.is_enabled" />
          <span>启用</span>
        </label>
      </FormRow>
    </div>

    <template #test>
      <TestBench :busy="test.busy" :ok="test.ok" run-label="检索测试" @run="runSearch">
        <div class="row" style="align-items: flex-end">
          <div style="flex: 1">
            <label class="field__label">检索语句（多条用 | 分隔，一次覆盖多个角度）</label>
            <input
              v-model="test.query"
              placeholder="如 大数据开发 实习要求 | Flink 状态管理 考点"
            />
          </div>
          <div style="width: 110px">
            <label class="field__label">Top K</label>
            <input type="number" v-model.number="test.topK" min="1" />
          </div>
        </div>
      </TestBench>

      <ul v-if="test.hits.length" style="list-style: none; padding: 0; margin: 12px 0 0">
        <li v-for="hit in test.hits" :key="hit.id" class="hit">
          <div class="row" style="justify-content: space-between">
            <span class="mono" style="color: var(--seal)">{{ hit.id }}</span>
            <span class="row" style="gap: 6px">
              <span v-if="hit.retrieval" class="pill" :class="{ 'pill--ok': hit.retrieval === 'both' }">
                {{ hit.retrieval }}
              </span>
              <span class="mono" style="color: var(--ink-3)">
                {{ hit.knowledge_base || hit.collection }} · score {{ (hit.score ?? 0).toFixed(4) }}
              </span>
            </span>
          </div>
          <div class="hit__text">{{ (hit.text || '').slice(0, 220) }}</div>
        </li>
      </ul>
      <p v-else-if="test.ok === false && test.error" class="notice notice--error" style="margin-top: 12px">
        {{ test.error }}
      </p>
      <p v-else-if="test.ok === false" class="notice" style="margin-top: 12px">
        没有命中：collection 可能是空的，或 id 前缀过滤太严。
      </p>
    </template>
  </ResourceWorkbench>
</template>

<style scoped>
.hit {
  border: 1px solid var(--rule);
  border-left: 2px solid var(--seal);
  padding: 8px 10px;
  margin-bottom: 8px;
  background: #fbf9f4;
}
.hit__text {
  font-family: var(--font-serif);
  font-size: 13px;
  margin-top: 4px;
  color: var(--ink-2);
}
</style>
