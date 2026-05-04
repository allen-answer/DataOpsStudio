import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'
import router from './router'

const pinia = createPinia()
const app = createApp(App)

// 全局兜底 errorHandler —— 处理 ErrorBoundary 没接住的（如异步回调 / setInterval
// / Promise 没 await 的拒绝）。仅记录到 console 不让用户看到 stack
// trace；用户视角的降级 UI 由 ErrorBoundary 组件负责。
app.config.errorHandler = (err, instance, info) => {
  console.error('[vue:errorHandler]', err, info)
}

// window 级未捕获异常 / promise rejection 也兜一下，避免 DevTools 满屏红
if (typeof window !== 'undefined') {
  window.addEventListener('unhandledrejection', (event) => {
    console.error('[unhandledrejection]', event.reason)
  })
}

app.use(pinia).use(router).mount('#app')
