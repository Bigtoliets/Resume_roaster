<script setup lang="ts">
import type { ResourceRow } from '@/composables/useResource'
import { confirmDialog } from '@/composables/useDialog'

const props = defineProps<{
  title: string
  subtitle: string
  index: string
  items: ResourceRow[]
  selectedId: number | null
  draftName?: string
  isNew: boolean
  isLoading: boolean
  isSaving: boolean
  message: string
  error: string
  search: string
  locked?: boolean
  /** 传 false 隐藏「复制」（Skills 页不可复制：正文在文件里，复制没有意义） */
  allowDuplicate?: boolean
  /** 传 false 隐藏「删除」（Skills 页只允许删孤儿行，由页面按行决定） */
  allowDelete?: boolean
}>()

const emit = defineEmits<{
  'update:search': [string]
  create: []
  select: [ResourceRow]
  save: []
  refresh: []
  remove: []
  duplicate: []
}>()

async function askDelete() {
  const ok = await confirmDialog({
    title: `删除「${props.draftName || '这条配置'}」？`,
    description:
      '删除后不再出现在列表里；若这条配置还被 Agent 或工具引用，后端会拒绝并告诉你引用方，' +
      '你可以选择「解绑并删除」。',
    confirmText: '删除',
    danger: true,
  })
  if (ok) emit('remove')
}

function displayName(item: ResourceRow): string {
  const shown = item.display_name
  return typeof shown === 'string' && shown ? shown : item.name
}
</script>

<template>
  <section>
    <header class="rise" style="margin-bottom: 18px; max-width: 78ch">
      <div class="eyebrow">{{ index }} · 平台资源</div>
      <h1 style="font-size: 26px; margin: 6px 0 6px">{{ title }}</h1>
      <p style="color: var(--ink-2); margin: 0">{{ subtitle }}</p>
    </header>

    <!-- 预留高度的提示槽：保存提示出现/消失时不再挤压下方内容
         （此前提示会把编辑器和"保存"按钮整体下移，连续保存时按钮会跳、容易点空） -->
    <div class="notice-slot">
      <p v-if="message" class="notice" style="border-color: var(--ok)">
        {{ message }}
      </p>
      <p v-else-if="error" class="notice notice--error" style="white-space: pre-wrap">
        {{ error }}
      </p>
    </div>

    <div class="workbench">
      <!-- 左：清单 -->
      <aside class="card workbench__list">
        <div class="workbench__list-head">
          <input
            :value="search"
            placeholder="搜索名称 / 描述"
            @input="emit('update:search', ($event.target as HTMLInputElement).value)"
          />
          <div class="row" style="gap: 6px">
            <button
              class="btn btn--ghost btn--sm"
              type="button"
              title="从后端重新拉取（数据库里的最新配置）"
              @click="emit('refresh')"
            >
              刷新
            </button>
            <button class="btn btn--sm" type="button" @click="emit('create')">＋ 新建</button>
          </div>
        </div>
        <div class="workbench__items">
          <button
            v-for="item in items"
            :key="item.id"
            class="workbench__item"
            :class="{ 'is-active': item.id === selectedId }"
            type="button"
            @click="emit('select', item)"
          >
            <span class="workbench__item-name">{{ displayName(item) }}</span>
            <span class="workbench__item-meta">
              <span class="mono">#{{ item.id }}</span>
              <span
                v-if="item.is_orphan"
                class="pill pill--warn"
                style="padding: 0 6px"
                title="表里有这行，但 backend/skill/ 下已经没有对应目录"
              >
                孤儿
              </span>
              <span
                v-if="item.is_enabled === false"
                class="pill pill--warn"
                style="padding: 0 6px"
              >
                已停用
              </span>
            </span>
            <span v-if="item.description" class="workbench__item-desc">{{ item.description }}</span>
          </button>
          <div v-if="!items.length && !isLoading" class="empty" style="padding: 22px 12px">
            还没有数据，点「新建」开始。
          </div>
        </div>
      </aside>

      <!-- 右：编辑器 -->
      <section class="card workbench__editor">
        <div class="card__head" style="align-items: center">
          <div>
            <span class="card__title">{{ isNew ? '新建条目' : draftName || '编辑' }}</span>
            <div v-if="!isNew" class="eyebrow" style="margin-top: 2px">ID #{{ selectedId }}</div>
          </div>
          <div class="row" style="gap: 8px">
            <button
              v-if="allowDuplicate !== false"
              class="btn btn--ghost btn--sm"
              type="button"
              :disabled="isNew"
              @click="emit('duplicate')"
            >
              复制
            </button>
            <template v-if="!isNew && allowDelete !== false">
              <button class="btn btn--ghost btn--sm" type="button" @click="askDelete">
                删除
              </button>
            </template>
            <button class="btn btn--sm" type="button" :disabled="isSaving || locked" @click="emit('save')">
              {{ isSaving ? '保存中…' : isNew ? '创建' : '保存' }}
            </button>
          </div>
        </div>

        <div class="card__body">
          <slot />
        </div>

        <div v-if="$slots.test" class="card__body" style="border-top: 1px dashed var(--rule-strong)">
          <div class="eyebrow" style="margin-bottom: 8px">测试场 · 与线上同一执行入口</div>
          <slot name="test" />
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.notice-slot {
  /* 固定高度，避免"提示出现 → 页面下移 → 按钮跳走"的布局位移 */
  min-height: 46px;
  margin-bottom: 10px;
}

.notice-slot .notice {
  margin: 0;
}
</style>
