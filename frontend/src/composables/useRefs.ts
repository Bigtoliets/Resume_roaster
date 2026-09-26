import { reactive } from 'vue'

import { api, ApiError } from '@/api/client'
import type { ResourceRow } from '@/composables/useResource'

interface CacheEntry {
  items: ResourceRow[]
  loading: boolean
  error: string
}

const cache = reactive<Record<string, CacheEntry>>({})

function entry(slug: string): CacheEntry {
  if (!cache[slug]) {
    cache[slug] = { items: [], loading: false, error: '' }
    void refresh(slug)
  }
  return cache[slug]
}

/** 拉取某类资源用于下拉/多选（带缓存，供引用型字段使用）。 */
export async function refresh(slug: string): Promise<void> {
  const slot = (cache[slug] ??= { items: [], loading: false, error: '' })
  slot.loading = true
  slot.error = ''
  try {
    slot.items = await api.get<ResourceRow[]>(`/${slug}`)
  } catch (exc) {
    slot.error = exc instanceof ApiError ? String(exc.detail) : String(exc)
  } finally {
    slot.loading = false
  }
}

export function useRefs(slug: string) {
  return entry(slug)
}

/** 按 id 找名字，找不到时回退成 #id，保证界面永远不空。 */
export function labelOf(slug: string, id: number | null | undefined): string {
  if (!id) return ''
  const found = (cache[slug]?.items || []).find((item) => item.id === id)
  return found ? `${found.name}` : `#${id}`
}
