<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Activity, Play, Square, Zap, RefreshCw, AlertCircle, Clock, Workflow as WorkflowIcon } from 'lucide-vue-next'
import { apiGet, apiJson } from '../../api'
import { useNoticeStore } from '../../stores/notice'

const noticeStore = useNoticeStore()

const status = ref(null)
const loading = ref(false)
const submitting = ref(false)
const autoRefresh = ref(true)
let refreshTimer = null

const entries = computed(() => status.value?.entries || [])
const sensors = computed(() => status.value?.sensors || [])
const isRunning = computed(() => status.value?.running === true)

async function reload() {
  loading.value = true
  try {
    status.value = await apiGet('/api/scheduler/status')
  } catch (err) {
    noticeStore.setNotice(`加载调度器状态失败：${err.message || err}`)
  } finally {
    loading.value = false
  }
}

async function startScheduler() {
  submitting.value = true
  try {
    status.value = await apiJson('/api/scheduler/start', 'POST', {})
    noticeStore.setNotice('调度器已启动')
  } catch (err) {
    noticeStore.setNotice(`启动失败：${err.message || err}`)
  } finally {
    submitting.value = false
  }
}

async function stopScheduler() {
  if (!confirm('确认停止调度器？所有 cron / sensor 触发会暂停，已运行的 job 不受影响。')) return
  submitting.value = true
  try {
    status.value = await apiJson('/api/scheduler/stop', 'POST', {})
    noticeStore.setNotice('调度器已停止')
  } catch (err) {
    noticeStore.setNotice(`停止失败：${err.message || err}`)
  } finally {
    submitting.value = false
  }
}

async function tickNow() {
  submitting.value = true
  try {
    const result = await apiJson('/api/scheduler/tick', 'POST', {})
    const submitted = (result.submitted || []).length
    noticeStore.setNotice(submitted ? `tick 完成，触发 ${submitted} 个 run` : 'tick 完成，无任务可触发')
    status.value = result.status || status.value
  } catch (err) {
    noticeStore.setNotice(`tick 失败：${err.message || err}`)
  } finally {
    submitting.value = false
  }
}

function formatTimestamp(ts) {
  if (!ts) return '-'
  return ts.replace('T', ' ').slice(0, 19)
}

onMounted(() => {
  reload()
  refreshTimer = setInterval(() => {
    if (autoRefresh.value) reload()
  }, 5000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <section class="space-y-6">
    <header class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800">调度器监控</h2>
        <p class="mt-1 text-sm text-slate-500">查看 cron / sensor 注册状态 + 触发历史；可临时启停或手动 tick</p>
      </div>
      <div class="flex items-center gap-2">
        <label class="flex items-center gap-1.5 text-xs text-slate-500">
          <input v-model="autoRefresh" type="checkbox" class="h-3.5 w-3.5" />
          5s 自动刷新
        </label>
        <button class="btn btn-outline gap-1.5" :disabled="loading" @click="reload">
          <RefreshCw class="h-4 w-4" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>
    </header>

    <!-- 总览 + 控制按钮 -->
    <div class="card p-5">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-4">
          <div class="grid h-12 w-12 place-items-center rounded-xl"
               :class="isRunning ? 'bg-status-success-bg text-status-success' : 'bg-slate-100 text-slate-400'">
            <Activity class="h-6 w-6" />
          </div>
          <div>
            <p class="text-base font-bold text-slate-800">
              {{ isRunning ? '运行中' : '已停止' }}
              <span class="muted ml-2 text-xs font-normal">
                后端：{{ status?.backend || '-' }} · 间隔：{{ status?.interval_seconds || '-' }}s
              </span>
            </p>
            <p class="muted text-xs">
              cron 任务 {{ entries.length }} 个 · sensor {{ sensors.length }} 个
            </p>
          </div>
        </div>
        <div class="flex gap-2">
          <button v-if="!isRunning" class="btn btn-primary gap-1.5" :disabled="submitting" @click="startScheduler">
            <Play class="h-4 w-4" /> 启动
          </button>
          <button v-else class="btn btn-danger gap-1.5" :disabled="submitting" @click="stopScheduler">
            <Square class="h-4 w-4" /> 停止
          </button>
          <button class="btn btn-outline gap-1.5" :disabled="submitting || !isRunning" @click="tickNow">
            <Zap class="h-4 w-4" /> 立即 tick
          </button>
        </div>
      </div>
    </div>

    <!-- Cron 任务 -->
    <div class="card overflow-hidden">
      <div class="flex items-center gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3">
        <Clock class="h-4 w-4 text-slate-500" />
        <h3 class="text-sm font-bold text-slate-700">Cron 任务 ({{ entries.length }})</h3>
      </div>
      <table class="w-full">
        <thead class="bg-white">
          <tr class="text-left text-xs uppercase tracking-wider text-slate-500">
            <th class="px-4 py-2 font-bold">作业流</th>
            <th class="px-4 py-2 font-bold">cron 表达式</th>
            <th class="px-4 py-2 font-bold">下次触发</th>
            <th class="px-4 py-2 font-bold">上次触发</th>
            <th class="px-4 py-2 font-bold">最近 job</th>
            <th class="px-4 py-2 font-bold">跳过重叠</th>
            <th class="px-4 py-2 font-bold">最近错误</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-if="!entries.length">
            <td colspan="7" class="px-4 py-8 text-center text-sm text-slate-400">
              暂无 cron 任务（创建作业流时设置 schedule_cron + status=active）
            </td>
          </tr>
          <tr v-for="e in entries" :key="e.workflow_id" class="text-sm">
            <td class="px-4 py-2 font-medium text-slate-800">
              <WorkflowIcon class="mr-1.5 inline h-3.5 w-3.5 text-primary" />
              {{ e.workflow_name }}
            </td>
            <td class="sql-font px-4 py-2 text-xs text-slate-600">{{ e.cron }}</td>
            <td class="sql-font px-4 py-2 text-xs text-slate-500">{{ formatTimestamp(e.next_run_at) }}</td>
            <td class="sql-font px-4 py-2 text-xs text-slate-500">{{ formatTimestamp(e.last_run_at) }}</td>
            <td class="sql-font px-4 py-2 text-xs text-slate-500">
              {{ e.last_job_id ? e.last_job_id.slice(0, 12) : '-' }}
            </td>
            <td class="px-4 py-2 text-xs">
              <span v-if="e.skipped_overlap" class="pill bg-status-warning-bg text-status-warning">
                {{ e.skipped_overlap }}
              </span>
              <span v-else class="muted">0</span>
            </td>
            <td class="px-4 py-2 text-xs">
              <span v-if="e.last_error" class="flex items-start gap-1 text-status-error">
                <AlertCircle class="h-3.5 w-3.5 shrink-0" />
                <span class="line-clamp-2">{{ e.last_error }}</span>
              </span>
              <span v-else class="muted">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Sensor 触发器 -->
    <div class="card overflow-hidden">
      <div class="flex items-center gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3">
        <Zap class="h-4 w-4 text-slate-500" />
        <h3 class="text-sm font-bold text-slate-700">Sensor 触发器 ({{ sensors.length }})</h3>
      </div>
      <table class="w-full">
        <thead class="bg-white">
          <tr class="text-left text-xs uppercase tracking-wider text-slate-500">
            <th class="px-4 py-2 font-bold">作业流</th>
            <th class="px-4 py-2 font-bold">类型</th>
            <th class="px-4 py-2 font-bold">轮询间隔</th>
            <th class="px-4 py-2 font-bold">下次检查</th>
            <th class="px-4 py-2 font-bold">最近触发</th>
            <th class="px-4 py-2 font-bold">最近 job</th>
            <th class="px-4 py-2 font-bold">最近错误</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-if="!sensors.length">
            <td colspan="7" class="px-4 py-8 text-center text-sm text-slate-400">
              暂无 sensor（在作业流配置 triggers 字段：file / workflow_success 等）
            </td>
          </tr>
          <tr v-for="s in sensors" :key="`${s.workflow_id}-${s.sensor_id}`" class="text-sm">
            <td class="px-4 py-2 font-medium text-slate-800">
              <WorkflowIcon class="mr-1.5 inline h-3.5 w-3.5 text-primary" />
              {{ s.workflow_name }}
            </td>
            <td class="px-4 py-2">
              <span class="pill bg-status-info-bg text-status-info">{{ s.type }}</span>
            </td>
            <td class="px-4 py-2 text-xs text-slate-500">
              {{ s.interval_seconds }}s
              <span v-if="s.cooldown_seconds" class="muted">/ cd {{ s.cooldown_seconds }}s</span>
            </td>
            <td class="sql-font px-4 py-2 text-xs text-slate-500">{{ formatTimestamp(s.next_run_at) }}</td>
            <td class="sql-font px-4 py-2 text-xs text-slate-500">{{ formatTimestamp(s.last_triggered_at) }}</td>
            <td class="sql-font px-4 py-2 text-xs text-slate-500">
              {{ s.last_job_id ? s.last_job_id.slice(0, 12) : '-' }}
            </td>
            <td class="px-4 py-2 text-xs">
              <span v-if="s.last_error" class="flex items-start gap-1 text-status-error">
                <AlertCircle class="h-3.5 w-3.5 shrink-0" />
                <span class="line-clamp-2">{{ s.last_error }}</span>
              </span>
              <span v-else class="muted">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
