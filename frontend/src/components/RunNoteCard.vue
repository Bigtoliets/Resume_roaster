<script setup lang="ts">
/**
 * 任务进度卡（D48）：把后端 `run_notes` 里的笔记渲染成人能一眼读懂的「任务状态」。
 *
 * 为什么值得单独做一张卡：上下文换页之后，模型看不到历史对话了，它唯一的依据就是
 * 这份笔记。用户能在这里看到"模型此刻到底记得什么"，出问题时也一眼能定位——
 * 是笔记写漏了，还是模型自己想岔了。
 */
import { computed, ref } from 'vue'

import type { RunNoteContent, RunNoteRow } from '@/api/types'

const props = defineProps<{ notes: RunNoteRow[]; defaultOpen?: boolean }>()

/**
 * D63：笔记改成**就地覆盖**，后端只会返回 0 或 1 条（一个会话一份），所以这里不再有
 * "版本下拉"——这张卡展示的永远是当前有效状态，也就是模型此刻真正记得的东西。
 * 想回看历史版本只能去 Langfuse：换页（`memory-rollover`）和回合收尾
 * （`turn-projection`）两个 span 的 output 里都带着当次的笔记。
 */
const active = computed(() => props.notes.at(-1) ?? null)
const content = computed<RunNoteContent>(() => active.value?.content ?? {})

/**
 * `progress.stage` 是**已完成的步骤列表**（每完成一步一条）。
 * 旧笔记里可能是字符串，或中间那版 `{stage, done, missing}`；这里统一成字符串数组再渲染。
 */
const steps = computed<string[]>(() => {
  const raw = content.value.progress
  if (!raw) return []
  if (typeof raw === 'string') return [raw]
  const stage = raw.stage
  if (Array.isArray(stage)) return stage
  // 中间那版：{stage: "…", done: [...], missing: [...]}
  const legacy = raw as { stage?: string; done?: string[]; missing?: string[] }
  return [
    ...(legacy.stage ? [legacy.stage] : []),
    ...(legacy.done || []),
    ...(legacy.missing || []).map((item) => `${item}（待确认）`),
  ]
})

const open = ref(props.defaultOpen ?? false)
</script>

<template>
  <div v-if="notes.length" class="note">
    <button class="note__head" type="button" @click="open = !open">
      <span class="note__badge">🧭</span>
      <span class="note__title">任务笔记</span>
      <span class="pill pill--seal">窗口 W{{ active?.window_index ?? 1 }}</span>
      <span v-if="steps.length" class="pill">{{ steps.length }} 步已完成</span>
      <span v-if="content.refs?.length" class="pill">{{ content.refs.length }} 条引用</span>
      <span class="spacer" />
      <span class="mono" style="color: var(--ink-3)">{{ open ? '收起 ▲' : '展开 ▼' }}</span>
    </button>

    <div v-if="open" class="note__body">
      <div class="note__cell">
        <div class="eyebrow">目标</div>
        <p class="note__text">{{ content.goal || '—' }}</p>
      </div>

      <div v-if="steps.length" class="note__cell">
        <div class="eyebrow">已完成的步骤</div>
        <ul class="note__list">
          <li v-for="(item, index) in steps" :key="index">{{ item }}</li>
        </ul>
      </div>

      <div v-if="content.plan?.length" class="note__cell">
        <div class="eyebrow">下一步</div>
        <ul class="note__list">
          <li v-for="(item, index) in content.plan" :key="index">{{ item }}</li>
        </ul>
      </div>

      <div v-if="content.refs?.length" class="note__cell">
        <div class="eyebrow">可回查引用（模型按指针取原文）</div>
        <ul class="note__list">
          <li v-for="(item, index) in content.refs" :key="index">
            <code class="note__pointer">{{ item.pointer }}</code>
            <span>{{ item.summary }}</span>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.note {
  margin: 0 0 12px;
  border: 1px dashed var(--rule-strong);
  border-radius: var(--radius);
  background: linear-gradient(0deg, var(--paper-2), var(--paper));
  overflow: hidden;
}

.note__head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: var(--font-ui);
  font-size: 12px;
  color: var(--ink-2);
  text-align: left;
}

.note__badge {
  font-size: 13px;
}

.note__title {
  font-family: var(--font-serif);
  font-size: 13px;
  color: var(--ink);
}

.note__body {
  padding: 4px 12px 12px;
  display: grid;
  gap: 10px;
}

.note__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
}

.note__cell {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.note__text {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink-2);
  white-space: pre-wrap;
}

.note__list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 4px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink-2);
}


.note__pointer {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 5px;
  margin-right: 6px;
  border: 1px solid var(--rule-strong);
  border-radius: 4px;
  color: var(--seal-ink);
  background: var(--seal-soft);
}

@media (max-width: 900px) {
  .note__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
