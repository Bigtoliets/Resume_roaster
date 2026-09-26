<script setup lang="ts">
import { computed } from 'vue'

interface Message {
  role: string
  content: string
  order?: number
}

const props = defineProps<{ modelValue: Message[] }>()
const emit = defineEmits<{ 'update:modelValue': [Message[]] }>()

const messages = computed<Message[]>(() => props.modelValue || [])

function update(next: Message[]) {
  emit(
    'update:modelValue',
    next.map((item, index) => ({ ...item, order: index + 1 })),
  )
}

function patch(index: number, key: keyof Message, value: string) {
  const next = messages.value.map((item, i) => (i === index ? { ...item, [key]: value } : item))
  update(next)
}

function add() {
  update([...messages.value, { role: 'user', content: '', order: messages.value.length + 1 }])
}

function removeAt(index: number) {
  update(messages.value.filter((_, i) => i !== index))
}

function move(index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= messages.value.length) return
  const next = [...messages.value]
  ;[next[index], next[target]] = [next[target], next[index]]
  update(next)
}

/** 从 content 里抓 {{变量}}，用于提示这个 Prompt 需要哪些变量 */
const detected = computed(() => {
  const found = new Set<string>()
  for (const item of messages.value) {
    for (const match of (item.content || '').matchAll(/\{\{\s*([a-zA-Z_][\w]*)\s*\}\}/g)) {
      found.add(match[1])
    }
  }
  return [...found]
})

const detectedLabels = computed(() => detected.value.map((name) => `{{${name}}}`))

defineExpose({ detected })
</script>

<template>
  <div>
    <div v-for="(item, index) in messages" :key="index" class="msg-row">
      <div class="msg-row__head">
        <select :value="item.role" @change="patch(index, 'role', ($event.target as HTMLSelectElement).value)">
          <option value="system">system</option>
          <option value="user">user</option>
          <option value="assistant">assistant（few-shot）</option>
        </select>
        <span class="mono" style="color: var(--ink-3)">#{{ index + 1 }}</span>
        <span class="spacer" />
        <button class="btn btn--ghost btn--sm" type="button" :disabled="index === 0" @click="move(index, -1)">↑</button>
        <button
          class="btn btn--ghost btn--sm"
          type="button"
          :disabled="index === messages.length - 1"
          @click="move(index, 1)"
        >
          ↓
        </button>
        <button class="btn btn--ghost btn--sm" type="button" @click="removeAt(index)">删除</button>
      </div>
      <textarea
        :rows="item.role === 'system' ? 6 : 5"
        spellcheck="false"
        :value="item.content"
        placeholder="在这里写提示词，变量用 {{变量名}}"
        @input="patch(index, 'content', ($event.target as HTMLTextAreaElement).value)"
      />
    </div>

    <div class="row" style="margin-top: 8px">
      <button class="btn btn--ghost btn--sm" type="button" @click="add">＋ 添加消息</button>
      <span class="spacer" />
      <span v-if="detectedLabels.length" class="hint">检测到变量：{{ detectedLabels.join(' ') }}</span>
    </div>
  </div>
</template>
