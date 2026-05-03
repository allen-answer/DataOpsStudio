<script setup>
import { computed, onMounted, ref } from 'vue'
import { RefreshCw, Filter } from 'lucide-vue-next'
import { apiGet } from '../../api'
import { useNoticeStore } from '../../stores/notice'

const noticeStore = useNoticeStore()

const logs = ref([])
const loading = ref(false)
const limit = ref(200)
const userFilter = ref('')
const methodFilter = ref('')
const resourceFilter = ref('')

const filteredLogs = computed(() => {
  return logs.value.filter(log => {
    if (userFilter.value && !log.username.toLowerCase().includes(userFilter.value.toLowerCase())) return false
    if (methodFilter.value && log.method !== methodFilter.value) return false
    if (resourceFilter.value && log.resource_type !== resourceFilter.value) return false
    return true
  })
})

const distinctResources = computed(() => {
  const set = new Set()
  logs.value.forEach(l => l.resource_type && set.add(l.resource_type))
  return [...set].sort()
})

const METHOD_COLORS = {
  POST:   'bg-green-100 text-green-700',
  PUT:    'bg-blue-100 text-blue-700',
  PATCH:  'bg-blue-100 text-blue-700',
  DELETE: 'bg-rose-100 text-rose-700',
}

function statusClass(code) {
  if (!code) return 'text-slate-400'
  if (code >= 200 && code < 300) return 'text-status-success'
  if (code >= 400 && code < 500) return 'text-status-warning'
  if (code >= 500) return 'text-status-error'
  return 'text-slate-500'
}

async function reload() {
  loading.value = true
  try {
    const data = await apiGet(`/api/audit-logs?limit=${limit.value}`)
    logs.value = Array.isArray(data?.logs) ? data.logs : []
  } catch (err) {
    noticeStore.setNotice(`加载审计日志失败：${err.message || err}`)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  reload()
})
</script>

<template>
  <section class="space-y-6">
    <header class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800">审计日志</h2>
        <p class="mt-1 text-sm text-slate-500">所有 mutating 操作 —— 来自 logs/audit.jsonl</p>
      </div>
      <div class="flex items-end gap-2">
        <label class="flex flex-col text-xs text-slate-500">
          条数上限
          <select v-model.number="limit" class="mt-1 w-24" @change="reload">
            <option :value="100">100</option>
            <option :value="200">200</option>
            <option :value="500">500</option>
            <option :value="1000">1000</option>
          </select>
        </label>
        <button class="btn btn-outline gap-1.5" @click="reload">
          <RefreshCw class="h-4 w-4" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>
    </header>

    <!-- 过滤器 -->
    <div class="card p-4">
      <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
        <label class="flex flex-col text-xs text-slate-500">
          <span class="mb-1 flex items-center gap-1"><Filter class="h-3 w-3" /> 用户名</span>
          <input v-model="userFilter" placeholder="模糊匹配" />
        </label>
        <label class="flex flex-col text-xs text-slate-500">
          <span class="mb-1 flex items-center gap-1"><Filter class="h-3 w-3" /> HTTP method</span>
          <select v-model="methodFilter">
            <option value="">全部</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
            <option value="DELETE">DELETE</option>
          </select>
        </label>
        <label class="flex flex-col text-xs text-slate-500">
          <span class="mb-1 flex items-center gap-1"><Filter class="h-3 w-3" /> 资源类型</span>
          <select v-model="resourceFilter">
            <option value="">全部</option>
            <option v-for="r in distinctResources" :key="r" :value="r">{{ r }}</option>
          </select>
        </label>
      </div>
    </div>

    <!-- 日志列表 -->
    <div class="card overflow-hidden">
      <table class="w-full">
        <thead class="bg-slate-50">
          <tr class="text-left text-xs uppercase tracking-wider text-slate-500">
            <th class="px-4 py-3 font-bold">时间</th>
            <th class="px-4 py-3 font-bold">用户</th>
            <th class="px-4 py-3 font-bold">Method</th>
            <th class="px-4 py-3 font-bold">资源</th>
            <th class="px-4 py-3 font-bold">Path</th>
            <th class="px-4 py-3 font-bold">Status</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-if="!filteredLogs.length">
            <td colspan="6" class="px-4 py-8 text-center text-sm text-slate-400">
              {{ loading ? '加载中…' : '暂无日志' }}
            </td>
          </tr>
          <tr v-for="(log, i) in filteredLogs" :key="i" class="text-sm">
            <td class="sql-font px-4 py-2 text-xs text-slate-500">{{ log.ts }}</td>
            <td class="px-4 py-2 font-medium text-slate-700">
              {{ log.username || '-' }}
              <span class="muted ml-1 text-[10px]">{{ log.user_id ? log.user_id.slice(0, 8) : '' }}</span>
            </td>
            <td class="px-4 py-2">
              <span class="pill" :class="METHOD_COLORS[log.method] || 'bg-slate-100 text-slate-600'">{{ log.method }}</span>
            </td>
            <td class="px-4 py-2">
              <span v-if="log.resource_type" class="pill bg-slate-100 text-slate-700">
                {{ log.resource_type }}
                <span v-if="log.resource_id" class="ml-1 text-slate-400">/ {{ log.resource_id.slice(0, 8) }}</span>
              </span>
              <span v-else class="muted">-</span>
            </td>
            <td class="sql-font px-4 py-2 text-xs text-slate-600">{{ log.path }}</td>
            <td class="px-4 py-2 text-right text-xs font-bold" :class="statusClass(log.status_code)">
              {{ log.status_code || '-' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
