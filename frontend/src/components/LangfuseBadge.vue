<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ url?: string; traceId?: string }>()

const enabled = computed(() => Boolean(props.url || props.traceId))
const href = computed(() => props.url || '')
</script>

<template>
  <a
    v-if="enabled"
    class="pill pill--seal mono"
    :href="href || undefined"
    target="_blank"
    rel="noreferrer"
    title="在 Langfuse 中查看该次运行的 trace"
  >
    ◉ trace
  </a>
  <span v-else class="pill" style="opacity: 0.45" title="Langfuse 未配置或未生成 trace">◌ trace</span>
</template>
