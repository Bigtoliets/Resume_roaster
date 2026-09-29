<script setup lang="ts">
/**
 * Skills（SD16 / SD22）：页面只展示 name + description，**只允许新建**。
 *
 * 正文在 `backend/skill/<name>/SKILL.md` 里，是这个 skill 的唯一真源；数据库那张表
 * 只是索引。所以页面不提供正文编辑——改了也会被启动对账覆盖回去。删除同理：
 * 只有"孤儿行"（磁盘上已经没有对应目录）才允许在这里删。
 */
import { computed, onMounted } from 'vue'

import FormRow from '@/components/form/FormRow.vue'
import ResourceWorkbench from '@/components/ResourceWorkbench.vue'
import { useResource } from '@/composables/useResource'

const {
  items,
  filtered,
  draft,
  selectedId,
  isNew,
  loading,
  saving,
  message,
  error,
  search,
  load,
  createNew,
  select,
  save,
  remove,
} = useResource<any>('skills', () => ({
  name: '',
  description: '',
  is_enabled: true,
}))

const selected = computed(() => items.value.find((item) => item.id === selectedId.value) || null)
const isOrphan = computed(() => Boolean(draft.value.is_orphan))
const skillPath = computed(() => `backend/skill/${draft.value.name || '<name>'}/SKILL.md`)

onMounted(load)
</script>

<template>
  <ResourceWorkbench
    index="05"
    title="Skills"
    subtitle="每份 skill 是一个目录：backend/skill/<name>/SKILL.md。页面只展示名字与描述 —— 正文请直接改文件，或交给蒸馏线路自动维护"
    :items="filtered"
    :selected-id="selectedId"
    :draft-name="draft.name"
    :is-new="isNew"
    :is-loading="loading"
    :is-saving="saving"
    :message="message"
    :error="error"
    :search="search"
    :locked="!isNew"
    :allow-duplicate="false"
    :allow-delete="isOrphan"
    @update:search="search = $event"
    @create="createNew"
    @select="select"
    @save="save()"
    @refresh="load"
    @remove="remove"
  >
    <div class="grid grid--2">
      <FormRow
        label="名称"
        required
        hint="小写字母/数字/连字符；必须与目录名一致（backend/skill/<name>/）"
      >
        <input v-model="draft.name" :disabled="!isNew" placeholder="如 redis-cache-review" />
      </FormRow>
      <FormRow label="描述" hint="说清「做什么」与「什么时候该用它」——它是触发这个 skill 的唯一指针">
        <input
          v-model="draft.description"
          :disabled="!isNew"
          placeholder="如：审简历里出现 Redis 时必须追问的四个场景"
        />
      </FormRow>
    </div>

    <div class="card" style="background: var(--paper-2); border-style: dashed">
      <div class="card__body">
        <div v-if="isNew" class="eyebrow" style="margin-bottom: 8px">新建说明</div>
        <div v-else class="eyebrow" style="margin-bottom: 8px">正文不在页面编辑</div>

        <p class="hint" style="margin: 0">
          新建时后端会在
          <code class="mono">{{ skillPath }}</code>
          下创建目录并写一份最小 SKILL.md（frontmatter + 占位正文）。
        </p>
        <p class="hint" style="margin: 6px 0 0">
          之后要改正文，直接编辑那个文件即可；<strong>页面不提供正文编辑</strong>——
          文件是唯一真源，数据库那张表只是索引，页面上改的值会在下次启动对账时被覆盖回去。
        </p>
        <p v-if="isOrphan" class="hint" style="margin: 6px 0 0; color: var(--seal-ink)">
          ⚠ 这条是孤儿行：磁盘上已经没有
          <code class="mono">backend/skill/{{ draft.name }}/</code>
          了。可以在这里删掉这行；要恢复内容请自己把目录放回去。
        </p>
        <p v-else-if="!isNew" class="hint" style="margin: 6px 0 0">
          要清理磁盘上的目录，请手动删除
          <code class="mono">backend/skill/{{ draft.name }}/</code>
          —— 删完这里会变成孤儿行，那时才能从页面删。
        </p>
      </div>
    </div>
  </ResourceWorkbench>
</template>
