<script setup lang="ts">
/** 助手回复的 markdown 渲染：marked 解析 + DOMPurify 清理（模型输出不可信）。 */
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed } from 'vue'

const props = defineProps<{ text: string; cacheKey?: string | number }>()

marked.setOptions({ breaks: true, gfm: true })

/** 同一条消息反复渲染（来回切会话）时复用解析结果，省掉重复的 marked + DOMPurify 开销 */
const renderedCache = new Map<string, string>()

const html = computed(() => {
  const key = `${props.cacheKey ?? ''}|${props.text.length}|${props.text.slice(0, 24)}`
  const cached = renderedCache.get(key)
  if (cached !== undefined) return cached
  const value = DOMPurify.sanitize(marked.parse(props.text || '') as string)
  if (renderedCache.size > 300) renderedCache.clear()
  renderedCache.set(key, value)
  return value
})
</script>

<template>
  <div class="md" v-html="html" />
</template>
