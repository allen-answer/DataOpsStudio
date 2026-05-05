// Vitest 全局 setup —— jsdom 环境补缺失的 browser API + Pinia 测试默认值。
import { afterEach, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// 每个 case 自己一个干净的 Pinia，避免 store 状态跨 case 漏
beforeEach(() => {
  setActivePinia(createPinia())
})

// localStorage 在 jsdom 自带，但确保 token / project 等 key 不残留
afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

// fetch 在 jsdom 没有原生实现 —— 默认 stub 成空响应，单测里用 vi.fn().mockResolvedValue 覆盖
if (!globalThis.fetch) {
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    headers: new Map([['content-type', 'application/json']]),
    json: async () => ({}),
    text: async () => '',
  }))
}

// Vue Router 用 createWebHashHistory；测试一般 mock router，不真起
// 给 lucide-vue-next 的图标组件兜底（jsdom 不渲染 SVG path 时不报）
