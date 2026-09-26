import path from 'node:path'
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  // 直接复用项目根目录的 .env：FRONTEND_PORT / API_PORT 改一处，前后端一起跟随
  const env = loadEnv(mode, path.resolve(__dirname, '..'), '')
  const frontendPort = Number(env.FRONTEND_PORT || 5173)
  const apiPort = Number(env.API_PORT || 8000)

  return {
    plugins: [vue()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      // 注意：Vite 默认绑 ::1，浏览器请用 http://localhost:<port>（写 127.0.0.1 会连不上）
      port: frontendPort,
      // 端口被占时直接失败而不顺延：否则后端拼给 Playwright 的打印地址会失配
      strictPort: true,
      proxy: {
        '/api': {
          target: `http://127.0.0.1:${apiPort}`,
          changeOrigin: true,
          configure(proxy) {
            proxy.on('proxyRes', (proxyRes) => {
              const contentType = proxyRes.headers['content-type']
              if (typeof contentType === 'string' && contentType.includes('text/event-stream')) {
                proxyRes.headers['cache-control'] = 'no-cache'
              }
            })
          },
        },
      },
    },
  }
})
