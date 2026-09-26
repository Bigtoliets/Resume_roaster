<script setup lang="ts">
import { computed } from 'vue'

import { useRefs } from '@/composables/useRefs'

const props = defineProps<{
  slug: string
  modelValue: number[]
  emptyHint?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [number[]] }>()

const slot = useRefs(props.slug)
const selected = computed(() => new Set(props.modelValue || []))

/** 工具这类资源带「实现状态」：未实现/未指定时标出来，避免配了却调不动。 */
function implWarning(item: Record<string, unknown>): string {
  const status = item.impl_status
  if (status === 'unset') return '未指定实现'
  if (status === 'missing') return '未实现'
  return ''
}

function label(item: Record<string, unknown>): string {
  return String(item.display_name || item.name || '')
}

function toggle(id: number) {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  emit('update:modelValue', [...next])
}
</script>

<template>
  <div class="chips">
    <button
      v-for="item in slot.items"
      :key="item.id"
      class="chip"
      :class="{
        'is-on': selected.has(item.id),
        'chip--warn': Boolean(implWarning(item)),
      }"
      :title="implWarning(item) ? `这个工具${implWarning(item)}，会话里调用会失败` : ''"
      type="button"
      @click="toggle(item.id)"
    >
      <span class="chip__mark">{{ selected.has(item.id) ? '✓' : '＋' }}</span>
      {{ label(item) }}
      <span v-if="implWarning(item)" class="chip__warn">{{ implWarning(item) }}</span>
    </button>
    <div v-if="!slot.items.length && !slot.loading" class="hint">
      {{ emptyHint || `暂无可选 ${slug}` }}
    </div>
  </div>
</template>

<style scoped>
.chip--warn {
  border-style: dashed;
}

.chip__warn {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--seal-soft);
  color: var(--seal-ink);
}
</style>
