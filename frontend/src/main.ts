import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import { registerNavProgress } from './composables/useNavProgress'
import { prefetchViews } from './router/prefetch'
import './styles/theme.css'

registerNavProgress(router)
// 预取开关：dev 下模块编译只要 2~13ms，预取收益很小，却会和用户交互抢主线程。
// 需要时用 VITE_PREFETCH=on 打开（见 router/prefetch.ts）。
if (import.meta.env.VITE_PREFETCH === 'on') prefetchViews()

createApp(App).use(createPinia()).use(router).mount('#app')
