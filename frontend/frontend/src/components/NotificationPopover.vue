<script setup>
import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'
import { Bell, Square, CheckCircle2, AlertCircle, Loader, Inbox } from 'lucide-vue-next'

// 异步任务通知中心。点击 Bell 弹 popover，显示当前 asyncJob 的状态。
// 当前 App.vue 只跟踪一个 asyncJob，所以这里也只显示一条；多条以后再扩。
const { asyncJob, asyncStatus, cancelAsync } = inject('app', {
  asyncJob: ref(null),
  asyncStatus: ref(null),
  cancelAsync: () => {},
})

const open = ref(false)
const anchor = ref(null)

const isRunning = computed(() => {
  if (!asyncJob.value) return false
  const status = asyncStatus.value?.status
  return !status || !['success', 'failed', 'cancelled'].includes(status)
})

const statusLabel = computed(() => {
  const s = asyncStatus.value?.status
  if (!s) return '排队中'
  return {
    pending: '排队中',
    running: '运行中',
    success: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }[s] || s
})

const statusTone = computed(() => {
  const s = asyncStatus.value?.status
  if (s === 'success') return 'status-success'
  if (s === 'failed') return 'status-error'
  if (s === 'cancelled') return 'status-pending'
  return 'status-running'
})

const statusIcon = computed(() => {
  const s = asyncStatus.value?.status
  if (s === 'success') return CheckCircle2
  if (s === 'failed') return AlertCircle
  return Loader
})

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

function onOutside(event) {
  if (!open.value) return
  if (anchor.value && !anchor.value.contains(event.target)) close()
}

onMounted(() => {
  document.addEventListener('click', onOutside)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onOutside)
})

async function handleCancel() {
  if (typeof cancelAsync === 'function') {
    await cancelAsync()
  }
}
</script>

<template>
  <div ref="anchor" class="relative">
    <button
      type="button"
      class="btn btn-ghost relative h-9 w-9 px-0"
      :title="asyncJob ? `后台任务：${statusLabel}` : '通知（暂无后台任务）'"
      @click="toggle"
    >
      <Bell class="h-4 w-4" />
      <span
        v-if="isRunning"
        class="absolute right-1.5 top-1.5 grid h-2 w-2 place-items-center rounded-full bg-status-running ring-2 ring-white"
      ></span>
    </button>

    <!-- popover -->
    <div
      v-if="open"
      class="absolute right-0 top-full z-40 mt-2 w-80 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl"
    >
      <div class="border-b border-slate-100 px-4 py-2.5">
        <div class="text-sm font-semibold text-slate-800">后台任务</div>
        <p class="muted text-[11px]">当前账号下的异步执行状态</p>
      </div>

      <div v-if="!asyncJob" class="px-4 py-6 text-center text-sm text-slate-400">
        <Inbox class="mx-auto mb-2 h-6 w-6 text-slate-300" />
        暂无后台任务
        <p class="muted mt-1 text-[11px]">在数据对比页点"后台执行"启动任务后，会在这里看到状态</p>
      </div>

      <div v-else class="px-4 py-3">
        <div class="mb-2 flex items-center gap-2">
          <component :is="statusIcon" class="h-4 w-4 shrink-0" :class="isRunning ? 'animate-spin text-status-running' : ''" />
          <span class="status-badge" :class="statusTone">{{ statusLabel }}</span>
          <span class="muted truncate font-mono text-[11px]" :title="asyncJob.job_id">
            {{ (asyncJob.job_id || '').slice(0, 8) }}
          </span>
        </div>

        <div class="space-y-1 text-xs text-slate-600">
          <div v-if="asyncStatus?.stage" class="flex justify-between gap-2">
            <span class="muted">阶段</span>
            <span class="text-slate-800">{{ asyncStatus.stage }}</span>
          </div>
          <div v-if="asyncStatus?.progress != null" class="flex justify-between gap-2">
            <span class="muted">进度</span>
            <span class="text-slate-800">{{ asyncStatus.progress }}</span>
          </div>
          <div v-if="asyncStatus?.error" class="flex flex-col gap-1">
            <span class="muted">错误</span>
            <span class="break-words text-status-error">{{ asyncStatus.error }}</span>
          </div>
        </div>

        <button
          v-if="isRunning"
          class="btn btn-danger mt-3 h-7 w-full px-2 text-[11px]"
          @click="handleCancel"
        >
          <Square class="h-3 w-3" /> 取消任务
        </button>
      </div>
    </div>
  </div>
</template>
