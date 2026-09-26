import { type Ref, computed, ref } from 'vue'

import { api, ApiError } from '@/api/client'
import { confirmDialog } from '@/composables/useDialog'
import { cloneDeep } from '@/utils/clone'

export interface ResourceRow {
  id: number
  name: string
  description?: string
  is_enabled?: boolean
  [key: string]: unknown
}

export function describeError(exc: unknown): string {
  if (exc instanceof ApiError) {
    if (typeof exc.detail === 'string') return exc.detail
    const detail = exc.detail as { message?: string; referenced_by?: { name: string }[] }
    if (detail?.message) {
      const refs = (detail.referenced_by || []).map((item) => item.name).join('、')
      return refs ? `${detail.message}：${refs}` : detail.message
    }
    return JSON.stringify(exc.detail)
  }
  return String(exc)
}

/**
 * 资源列表的模块级缓存：切走再切回配置页时先渲染缓存内容，避免"白屏一下"。
 * 后台仍会重新拉取（后端有 60s TTL 缓存，成本很低）。
 */
const listCache = new Map<string, { at: number; items: ResourceRow[] }>()
const LIST_TTL = 30_000

/**
 * 统一的资源 CRUD：列表 / 选中 / 新建 / 保存 / 删除 / 复制。
 * 六个配置页共用这一套逻辑，页面只管各自的表单长什么样。
 */
export function useResource<T extends ResourceRow>(slug: string, defaults: () => Partial<T>) {
  const cached = listCache.get(slug)
  const fresh = cached && Date.now() - cached.at < LIST_TTL ? cached.items : []
  // 显式标注为 Ref<T[]>：否则 Vue 的 UnwrapRefSimple 会让 items.value 的赋值被判为类型不兼容
  const items = ref<T[]>((fresh as T[]) ?? []) as Ref<T[]>
  const draft = ref<Partial<T>>(defaults())
  const selectedId = ref<number | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const message = ref('')
  const error = ref('')
  const search = ref('')

  const isNew = computed(() => selectedId.value === null)

  const filtered = computed(() => {
    const keyword = search.value.trim().toLowerCase()
    if (!keyword) return items.value
    return items.value.filter(
      (item) =>
        item.name.toLowerCase().includes(keyword) ||
        String(item.description || '')
          .toLowerCase()
          .includes(keyword),
    )
  })

  async function load() {
    // 有缓存就先不显示 loading，界面不会闪
    loading.value = items.value.length === 0
    error.value = ''
    try {
      items.value = await api.get<T[]>(`/${slug}`)
      listCache.set(slug, { at: Date.now(), items: items.value as ResourceRow[] })
    } catch (exc) {
      error.value = describeError(exc)
    } finally {
      loading.value = false
    }
  }

  function createNew() {
    selectedId.value = null
    draft.value = cloneDeep(defaults())
    message.value = ''
    error.value = ''
  }

  function select(row: T) {
    selectedId.value = row.id
    draft.value = cloneDeep(row)
    message.value = ''
    error.value = ''
  }

  function duplicate() {
    if (selectedId.value === null) return
    const copy = cloneDeep(draft.value)
    copy.name = `${String(copy.name || '')}-副本`
    copy.id = undefined as unknown as number
    selectedId.value = null
    draft.value = copy
    message.value = '已复制为新条目，改名后保存即可'
  }

  async function save(payload?: Record<string, unknown>) {
    if (!String(draft.value.name || '').trim()) {
      error.value = '名称必填'
      return false
    }
    saving.value = true
    error.value = ''
    message.value = ''
    try {
      const body = payload ?? (draft.value as Record<string, unknown>)
      if (isNew.value) {
        const created = await api.post<T>(`/${slug}`, body)
        // 乐观更新：直接把新条目插到列表前面，不再多发一次列表请求
        items.value = [created, ...items.value]
        listCache.set(slug, { at: Date.now(), items: items.value as ResourceRow[] })
        select(created)
        message.value = '已创建'
      } else {
        const updated = await api.put<T>(`/${slug}/${selectedId.value}`, body)
        items.value = items.value.map((item) => (item.id === updated.id ? updated : item))
        listCache.set(slug, { at: Date.now(), items: items.value as ResourceRow[] })
        select(updated)
        message.value = '已保存'
      }
      return true
    } catch (exc) {
      error.value = describeError(exc)
      return false
    } finally {
      saving.value = false
    }
  }

  /**
   * 删除。被引用时后端返回 409，此时弹出二次确认，确认后带 cascade 重试
   * （后端会把这条资源从所有引用方自动解绑，再软删）。
   */
  async function remove(force = false) {
    if (selectedId.value === null) return
    const removedId = selectedId.value
    const snapshot = items.value
    error.value = ''
    message.value = ''

    try {
      await api.del(`/${slug}/${removedId}${force ? '?cascade=true' : ''}`)
    } catch (exc) {
      if (!force && exc instanceof ApiError && exc.status === 409) {
        const detail = exc.detail as { referenced_by?: { name?: string }[] } | string
        const refs =
          typeof detail === 'object'
            ? (detail?.referenced_by || [])
                .map((item) => item?.name)
                .filter(Boolean) as string[]
            : []
        const ok = await confirmDialog({
          title: '这条配置正被使用，解绑并删除？',
          description: refs.length
            ? `引用方：${refs.join('、')}。删除后会从这些位置自动解绑，它们将退回默认行为。`
            : '删除后会从引用它的配置里自动解绑。',
          confirmText: '解绑并删除',
          danger: true,
        })
        if (ok) {
          await remove(true)
          return
        }
        error.value = describeError(exc)
        return
      }
      // 真的失败：回滚本地列表
      items.value = snapshot
      listCache.set(slug, { at: Date.now(), items: snapshot as ResourceRow[] })
      error.value = describeError(exc)
      return
    }

    // 成功后才移除本地条目，避免"先消失再闪回来"的错觉
    items.value = items.value.filter((item) => item.id !== removedId)
    listCache.set(slug, { at: Date.now(), items: items.value as ResourceRow[] })
    createNew()
    message.value = force ? '已解绑并删除' : '已删除'
  }

  return {
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
    duplicate,
    save,
    remove,
  }
}
