<script setup lang="ts">
import { computed, onMounted } from 'vue'

import FormRow from '@/components/form/FormRow.vue'
import ResourceWorkbench from '@/components/ResourceWorkbench.vue'
import { useResource } from '@/composables/useResource'

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
} = useResource<any>('skills', () => ({
  name: '',
  description: '',
  instructions: '',
  is_enabled: true,
}))

const instructionLines = computed(() => (draft.value.instructions || '').split('\n').length)

onMounted(load)
</script>

<template>
  <ResourceWorkbench
    index="05"
    title="Skills"
    subtitle="独立的文本增强单元：只写「什么条件下必须检查什么」。不引用任何其他资源——组合发生在 Agent 上"
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
        <input v-model="draft.name" placeholder="如 缓存方案拷问" />
      </FormRow>
      <FormRow label="描述">
        <input v-model="draft.description" placeholder="这个 Skill 解决什么问题" />
      </FormRow>
    </div>

    <FormRow
      label="指令（markdown）"
      hint="会被追加到 Agent 的 system 段。写清楚「什么条件下必须追问什么」，越具体越有效"
    >
      <textarea
        rows="12"
        spellcheck="false"
        v-model="draft.instructions"
        placeholder="示例：&#10;若简历出现 Redis/缓存字样，必须逐一追问：&#10;1) 缓存穿透如何防护（布隆过滤器 / 空值缓存）&#10;2) 缓存雪崩如何避免（过期时间打散 / 多级缓存）&#10;3) 热点 key 击穿如何处理（互斥锁 / 逻辑过期）&#10;4) 缓存与数据库一致性策略（延迟双删 / 订阅 binlog）&#10;未提及的场景，记为该技能「只有名词没有场景」。"
      />
      <div class="hint">{{ instructionLines }} 行 · 约 {{ (draft.instructions || '').length }} 字</div>
    </FormRow>

    <FormRow label="开关">
      <label class="row" style="gap: 6px">
        <input type="checkbox" style="width: auto" v-model="draft.is_enabled" />
        <span>启用</span>
      </label>
    </FormRow>
  </ResourceWorkbench>
</template>
