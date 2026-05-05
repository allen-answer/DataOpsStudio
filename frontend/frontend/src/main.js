import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'
import router from './router'
import { i18n, setLocale } from './i18n'

const pinia = createPinia()
const app = createApp(App)

// 启动时把 <html lang> 跟 i18n 的 locale 对齐 —— 屏幕阅读器 / SEO 友好
setLocale(i18n.global.locale.value)

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

app.use(pinia).use(router).use(i18n).mount('#app')
