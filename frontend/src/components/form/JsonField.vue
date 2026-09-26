<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: unknown
  rows?: number
  placeholder?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [unknown] }>()

const text = ref('')
const error = ref('')

watch(
  () => props.modelValue,
  (value) => {
    text.value = value === undefined || value === null ? '' : JSON.stringify(value, null, 2)
    error.value = ''
  },
  { immediate: true },
)

function commit() {
  const raw = text.value.trim()
  if (!raw) {
    error.value = ''
    emit('update:modelValue', null)
    return
  }
  try {
    emit('update:modelValue', JSON.parse(raw))
    error.value = ''
  } catch (exc) {
    error.value = `JSON 格式错误：${(exc as Error).message}`
  }
}

const valid = computed(() => !error.value)
</script>

<template>
  <div>
    <textarea
      :rows="rows ?? 6"
      :value="text"
      spellcheck="false"
      :placeholder="placeholder"
      @input="text = ($event.target as HTMLTextAreaElement).value; commit()"
    />
    <div class="row" style="justify-content: space-between; margin-top: 4px">
      <span class="hint" :style="valid ? undefined : 'color: var(--seal)'">
        {{ error || '合法 JSON' }}
      </span>
      <button
        class="btn btn--ghost btn--sm"
        type="button"
        :disabled="!valid || !text.trim()"
        @click="text = JSON.stringify(JSON.parse(text), null, 2)"
      >
        格式化
      </button>
    </div>
  </div>
</template>
