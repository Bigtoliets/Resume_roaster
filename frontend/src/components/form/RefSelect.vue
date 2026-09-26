<script setup lang="ts">
import { computed } from 'vue'

import { useRefs } from '@/composables/useRefs'

const props = defineProps<{
  slug: string
  modelValue: number | null
  emptyLabel?: string
  filter?: (item: { id: number; name: string; [key: string]: unknown }) => boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [number | null] }>()

const slot = useRefs(props.slug)
const options = computed(() =>
  props.filter ? slot.items.filter(props.filter) : slot.items,
)

function onChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  emit('update:modelValue', value ? Number(value) : null)
}
</script>

<template>
  <select :value="modelValue ?? ''" @change="onChange">
    <option value="">{{ emptyLabel || '— 不绑定 —' }}</option>
    <option v-for="item in options" :key="item.id" :value="item.id">
      {{ item.name }}（#{{ item.id }}）
    </option>
  </select>
  <div v-if="slot.error" class="hint" style="color: var(--seal)">加载失败：{{ slot.error }}</div>
  <div v-else-if="!slot.items.length && !slot.loading" class="hint">
    暂无可选 {{ slug }}，先去对应页面创建。
  </div>
</template>
