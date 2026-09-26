/**
 * 路由分包预加载。
 *
 * 每个页面都是懒加载的分包；dev 模式下第一次进入才会编译该页的模块，
 * 表现为"第一次点某个导航要顿一下"。空闲时提前 import，把这次编译摊到启动阶段。
 */

const LOADERS: (() => Promise<unknown>)[] = [
  () => import('@/views/OverviewView.vue'),
  () => import('@/views/SessionDetailView.vue'),
  () => import('@/views/ModelsView.vue'),
  () => import('@/views/PromptsView.vue'),
  () => import('@/views/ToolsView.vue'),
  () => import('@/views/KnowledgeBasesView.vue'),
  () => import('@/views/SkillsView.vue'),
  () => import('@/views/AgentsView.vue'),
]

/** 常用接口预热：进任意配置页都能立刻出内容（后端有 60s 缓存，成本很低） */
const API_WARMUP = [
  '/sessions',
  '/agents',
  '/models',
  '/prompts',
  '/tools',
  '/knowledge-bases',
  '/skills',
]

async function warmApi(): Promise<void> {
  const { api } = await import('@/api/client')
  await Promise.all(API_WARMUP.map((path) => api.get(path).catch(() => undefined)))
}

export function prefetchViews(): void {
  const run = async () => {
    // 串行 + 逐个让出主线程：原先并行 9 个动态 import 会在首屏后形成一次请求洪峰
    // （实测首屏 load 75ms，但 networkidle 要再等 624ms），抢的是用户第一次点击。
    for (const load of LOADERS) {
      try {
        await load()
      } catch {
        // 失败无所谓：真正导航时还会再加载一次
      }
      await new Promise((resolve) => window.setTimeout(resolve, 16))
    }
    await warmApi()
  }

  // 等首屏彻底空闲再预热，避免与用户第一次交互抢带宽
  const start = () => {
    const idle = (
      window as unknown as {
        requestIdleCallback?: (cb: () => void, options?: { timeout?: number }) => number
      }
    ).requestIdleCallback
    if (typeof idle === 'function') idle(run, { timeout: 2000 })
    else window.setTimeout(run, 800)
  }
  if (document.readyState === 'complete') start()
  else window.addEventListener('load', start, { once: true })
}
