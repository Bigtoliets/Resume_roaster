<script setup lang="ts">
defineProps<{ label: string; hint?: string; required?: boolean }>()
</script>

<template>
  <div class="field">
    <label class="field__label">
      {{ label }}
      <span v-if="required" style="color: var(--seal)">*</span>
    </label>
    <slot />
    <div v-if="hint" class="hint">{{ hint }}</div>
  </div>
</template>
