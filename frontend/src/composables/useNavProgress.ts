/** 路由切换进度：让"正在切页"这件事可见，避免瞬间空白被当成卡死。 */

import { ref } from 'vue'
import type { Router } from 'vue-router'

export const navigating = ref(false)

let timer: number | undefined

export function registerNavProgress(router: Router): void {
  router.beforeEach(() => {
    navigating.value = true
    window.clearTimeout(timer)
    // 兜底：万一 afterEach 没触发（导航被取消），1.5 秒后自己收起
    timer = window.setTimeout(() => (navigating.value = false), 1500)
  })
  router.afterEach(() => {
    window.clearTimeout(timer)
    // 稍微延迟收起，避免快速切换时进度条闪烁
    timer = window.setTimeout(() => (navigating.value = false), 120)
  })
  router.onError(() => (navigating.value = false))
}
