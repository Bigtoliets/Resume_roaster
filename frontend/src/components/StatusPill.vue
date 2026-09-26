<script setup lang="ts">
defineProps<{ label: string; state?: 'ok' | 'warn' | 'idle' | 'seal'; title?: string }>()
</script>

<template>
  <span class="pill" :class="{ 'pill--ok': state === 'ok', 'pill--warn': state === 'warn', 'pill--seal': state === 'seal' }" :title="title">
    <slot>{{ label }}</slot>
  </span>
</template>
