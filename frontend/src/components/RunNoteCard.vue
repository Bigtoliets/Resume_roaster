<script setup lang="ts">
/**
 * 任务进度卡（D48）：把后端 `run_notes` 里的笔记渲染成人能一眼读懂的「任务状态」。
 *
 * 为什么值得单独做一张卡：上下文换页之后，模型看不到历史对话了，它唯一的依据就是
 * 这份笔记。用户能在这里看到"模型此刻到底记得什么"，出问题时也一眼能定位——
 * 是笔记写漏了，还是模型自己想岔了。
 */
import { computed, ref, watch } from 'vue'

import type { RunNoteContent, RunNoteRow } from '@/api/types'

const props = defineProps<{ notes: RunNoteRow[]; defaultOpen?: boolean }>()

/** 默认看**最后一条**（= 当前有效状态）；用户可以在下拉里回看历史版本。 */
const activeId = ref<number | null>(props.notes.at(-1)?.id ?? null)
watch(
  () => props.notes,
  (list) => {
    // 列表更新（新一轮换页）时自动跳到最新那条，除非用户在手动回看历史
    const latest = list.at(-1)
    if (latest && !list.some((item) => item.id === activeId.value)) activeId.value = latest.id
  },
)

const active = computed(
  () => props.notes.find((item) => item.id === activeId.value) ?? props.notes.at(-1) ?? null,
)
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
      <span class="note__title">任务笔记 · 第 {{ notes.length }} 版</span>
      <span class="pill pill--seal">窗口 W{{ active?.window_index ?? 1 }}</span>
      <span v-if="steps.length" class="pill">{{ steps.length }} 步已完成</span>
      <span v-if="content.refs?.length" class="pill">{{ content.refs.length }} 条引用</span>
      <span class="spacer" />
      <span class="mono" style="color: var(--ink-3)">{{ open ? '收起 ▲' : '展开 ▼' }}</span>
    </button>

    <div v-if="open" class="note__body">
      <div v-if="notes.length > 1" class="note__picker">
        <span class="eyebrow">版本</span>
        <select v-model.number="activeId">
          <option v-for="(item, index) in notes" :key="item.id" :value="item.id">
            第 {{ index + 1 }} 版 · 窗口 W{{ item.window_index }}
          </option>
        </select>
        <span class="hint" style="margin: 0">最后一版才是当前状态，前面的只作回看</span>
      </div>

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

.note__picker {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.note__picker select {
  width: auto;
  padding: 3px 8px;
  font-size: 12px;
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
