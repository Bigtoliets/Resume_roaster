<script setup lang="ts">
/** 全局对话框宿主：确认删除、重命名等都用它，保证风格统一。 */
import { nextTick, ref, watch } from 'vue'

import { dialogState, resolveDialog } from '@/composables/useDialog'

const inputRef = ref<HTMLInputElement | null>(null)
const confirmRef = ref<HTMLButtonElement | null>(null)

watch(
  () => dialogState.open,
  async (open) => {
    if (!open) return
    dialogState.error = ''
    await nextTick()
    if (dialogState.mode === 'prompt') inputRef.value?.focus()
    else confirmRef.value?.focus()
  },
)

function cancel() {
  resolveDialog(dialogState.mode === 'prompt' ? null : false)
}

function submit() {
  if (dialogState.mode === 'prompt') {
    const value = dialogState.value.trim()
    if (dialogState.required && !value) {
      dialogState.error = dialogState.hint || '这一项不能为空'
      return
    }
    resolveDialog(value)
    return
  }
  resolveDialog(true)
}
</script>

<template>
  <Transition name="dialog">
    <div v-if="dialogState.open" class="dialog-mask" @click.self="cancel">
      <div
        class="dialog"
        role="dialog"
        aria-modal="true"
        :aria-label="dialogState.title"
        @keydown.esc.stop="cancel"
        @keydown.enter.prevent="submit"
      >
        <div class="dialog__head">
          <span class="dialog__seal" :class="{ 'dialog__seal--danger': dialogState.danger }">
            {{ dialogState.danger ? '慎' : '问' }}
          </span>
          <div style="min-width: 0">
            <div class="eyebrow">{{ dialogState.danger ? '不可撤销的操作' : '请确认' }}</div>
            <h3 class="dialog__title">{{ dialogState.title }}</h3>
          </div>
        </div>

        <p v-if="dialogState.description" class="dialog__desc">{{ dialogState.description }}</p>

        <div v-if="dialogState.mode === 'prompt'" style="margin-top: 14px">
          <label class="field__label">{{ dialogState.label || '内容' }}</label>
          <input
            ref="inputRef"
            v-model="dialogState.value"
            :placeholder="dialogState.placeholder"
            @keydown.enter.prevent="submit"
          />
          <div v-if="dialogState.error" class="hint" style="color: var(--seal)">
            {{ dialogState.error }}
          </div>
          <div v-else-if="dialogState.hint" class="hint">{{ dialogState.hint }}</div>
        </div>

        <div class="dialog__foot">
          <button class="btn btn--ghost btn--sm" type="button" @click="cancel">
            {{ dialogState.cancelText }}
          </button>
          <button
            ref="confirmRef"
            class="btn btn--sm"
            :class="{ 'btn--seal': dialogState.danger }"
            type="button"
            @click="submit"
          >
            {{ dialogState.confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.dialog-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  background: rgba(26, 24, 21, 0.42);
  backdrop-filter: blur(3px);
}

.dialog {
  width: min(430px, calc(100vw - 40px));
  background: var(--paper);
  border: 1px solid var(--rule-strong);
  box-shadow: 0 24px 60px -24px rgba(26, 24, 21, 0.55);
  border-radius: 4px;
  padding: 18px 20px 16px;
}

.dialog__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.dialog__seal {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border: 2px solid var(--ink);
  border-radius: 10px;
  font-family: var(--font-serif);
  font-size: 15px;
  color: var(--ink);
  transform: rotate(-6deg);
}

.dialog__seal--danger {
  border-color: var(--seal);
  color: var(--seal-ink);
  background: var(--seal-soft);
}

.dialog__title {
  font-size: 17px;
  margin-top: 2px;
  line-height: 1.45;
  word-break: break-word;
}

.dialog__desc {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--ink-2);
}

.dialog__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px solid var(--rule);
}

.dialog-enter-active,
.dialog-leave-active {
  transition: opacity 0.16s ease;
}

.dialog-enter-active .dialog,
.dialog-leave-active .dialog {
  transition: transform 0.18s cubic-bezier(0.22, 0.68, 0.34, 1), opacity 0.18s ease;
}

.dialog-enter-from,
.dialog-leave-to {
  opacity: 0;
}

.dialog-enter-from .dialog,
.dialog-leave-to .dialog {
  transform: translateY(8px) scale(0.98);
  opacity: 0;
}
</style>
