<script setup lang="ts">
defineProps<{
  busy?: boolean
  output?: string
  ok?: boolean | null
  runLabel?: string
}>()

const emit = defineEmits<{ run: [] }>()
</script>

<template>
  <div>
    <slot />
    <div class="row" style="margin-top: 10px">
      <button class="btn btn--seal btn--sm" type="button" :disabled="busy" @click="emit('run')">
        {{ busy ? '执行中…' : runLabel || '执行' }}
      </button>
      <span v-if="ok === true" class="pill pill--ok">成功</span>
      <span v-else-if="ok === false" class="pill pill--seal">失败</span>
    </div>
    <pre v-if="output" class="test-output">{{ output }}</pre>
  </div>
</template>
