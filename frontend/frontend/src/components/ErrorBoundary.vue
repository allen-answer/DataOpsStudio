<script setup>
// 全局 ErrorBoundary —— 捕获子组件渲染 / setup 异常，显示降级 UI 而不是整页
// 白屏。Vue 3 `onErrorCaptured` 钩子返回 false 阻止异常继续冒泡。
//
// 注意 errorCaptured 只能捕获子组件错误，不能捕异步回调（setTimeout / Promise
// 没 await 等）。那部分由 main.js 的 app.config.errorHandler 兜住。
import { onErrorCaptured, ref } from 'vue'
import { AlertTriangle, RotateCw } from 'lucide-vue-next'

const props = defineProps({
  // 给 boundary 命个名，多个 boundary 嵌套时调试看 stack 用
  name: { type: String, default: 'app' },
})

const error = ref(null)
const errorInfo = ref('')

onErrorCaptured((err, instance, info) => {
  error.value = err
  errorInfo.value = info || ''
  console.error(`[ErrorBoundary:${props.name}] captured:`, err, info)
  // 返回 false 表示我们处理了 —— 不让它继续冒泡到 app.config.errorHandler
  return false
})

function reset() {
  error.value = null
  errorInfo.value = ''
}

function reload() {
  window.location.reload()
}
</script>

<template>
  <slot v-if="!error" />
  <div v-else class="card mx-auto my-8 max-w-2xl border-status-error-bg bg-status-error-bg/30 p-6">
    <div class="flex items-start gap-3">
      <div class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-status-error-bg text-status-error">
        <AlertTriangle class="h-5 w-5" />
      </div>
      <div class="min-w-0 flex-1">
        <h3 class="text-lg font-bold text-status-error">页面渲染出错</h3>
        <p class="muted mt-1 text-xs">
          组件抛了一个未捕获异常。可以试试"重置"或"刷新"。如果反复出现，把下面错误信息发给开发者。
        </p>
        <div class="mt-3 rounded border border-status-error-bg bg-white/60 p-3 text-xs">
          <p class="sql-font font-bold text-status-error">{{ error.name || 'Error' }}: {{ error.message || String(error) }}</p>
          <p v-if="errorInfo" class="muted mt-1 text-[11px]">触发位置：{{ errorInfo }}</p>
          <details v-if="error.stack" class="mt-2">
            <summary class="cursor-pointer muted text-[11px]">调用栈</summary>
            <pre class="sql-font mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-[10.5px] text-slate-600">{{ error.stack }}</pre>
          </details>
        </div>
        <div class="mt-4 flex gap-2">
          <button class="btn btn-outline gap-1.5" @click="reset">
            <RotateCw class="h-3.5 w-3.5" />
            重置
          </button>
          <button class="btn btn-primary gap-1.5" @click="reload">
            刷新页面
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
