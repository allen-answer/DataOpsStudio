<script setup>
import { computed, ref } from 'vue'
import { workflows, projects, statusMeta, scheduleStatusMeta, opsStats } from '../../mock/operations'

const emit = defineEmits(['open-dag', 'open-runs'])

const projectFilter = ref('all')
const statusFilter = ref('all')   // all / online / paused
const searchTerm = ref('')

const filtered = computed(() => {
  return workflows.filter((wf) => {
    if (projectFilter.value !== 'all' && wf.project !== projectFilter.value) return false
    if (statusFilter.value !== 'all' && wf.schedule_status !== statusFilter.value) return false
    if (searchTerm.value && !wf.name.includes(searchTerm.value) && !wf.owner.includes(searchTerm.value)) return false
    return true
  })
})

const projectName = (id) => projects.find((p) => p.id === id)?.name || id
const successRatePct = (rate) => `${Math.round(rate * 100)}%`
const successRateClass = (rate) => {
  if (rate >= 0.95) return 'text-emerald-600'
  if (rate >= 0.85) return 'text-amber-600'
  return 'text-rose-600'
}
</script>

<template>
  <div class="space-y-4">
    <!-- compact stats strip -->
    <div class="grid grid-cols-2 gap-3 md:grid-cols-5">
      <div class="rounded-md border border-slate-200 bg-white px-4 py-3">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">今日运行</p>
        <p class="mt-1 text-2xl font-bold text-slate-800">{{ opsStats.runs_today }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">成功 {{ opsStats.success_today }} · 失败 {{ opsStats.failed_today }}</p>
      </div>
      <div class="rounded-md border border-slate-200 bg-white px-4 py-3">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">运行中</p>
        <p class="mt-1 flex items-center gap-2 text-2xl font-bold text-sky-600">
          <span class="h-2.5 w-2.5 rounded-full bg-sky-500 animate-pulse"></span>
          {{ opsStats.running_now }}
        </p>
        <p class="mt-0.5 text-[11px] text-slate-500">实时</p>
      </div>
      <div class="rounded-md border border-slate-200 bg-white px-4 py-3">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">成功率</p>
        <p class="mt-1 text-2xl font-bold text-emerald-600">{{ Math.round(opsStats.success_today / opsStats.runs_today * 100) }}%</p>
        <p class="mt-0.5 text-[11px] text-slate-500">7 日 91%</p>
      </div>
      <div class="rounded-md border border-slate-200 bg-white px-4 py-3">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">平均耗时</p>
        <p class="mt-1 text-2xl font-bold text-slate-800">{{ opsStats.avg_duration }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">↑ 7 日 +8%</p>
      </div>
      <div class="rounded-md border border-slate-200 bg-white px-4 py-3">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">失败趋势 (7d)</p>
        <div class="mt-1.5 flex h-7 items-end gap-1">
          <span
            v-for="(value, idx) in opsStats.failure_trend_7d" :key="idx"
            class="flex-1 rounded-sm"
            :class="value >= 5 ? 'bg-rose-400' : (value >= 3 ? 'bg-amber-400' : 'bg-slate-300')"
            :style="{ height: `${Math.min(100, value * 12 + 8)}%` }"
            :title="`第 ${idx + 1} 天: ${value}`"
          ></span>
        </div>
      </div>
    </div>

    <!-- toolbar -->
    <div class="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 bg-white px-3 py-2.5">
      <div class="flex items-center gap-2 text-sm">
        <span class="text-[11px] font-semibold uppercase text-slate-400">项目</span>
        <select v-model="projectFilter" class="h-8 rounded border-slate-300 bg-white px-2 text-sm">
          <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }} ({{ p.count }})</option>
        </select>
      </div>
      <div class="flex items-center gap-2 text-sm">
        <span class="text-[11px] font-semibold uppercase text-slate-400">调度</span>
        <select v-model="statusFilter" class="h-8 rounded border-slate-300 bg-white px-2 text-sm">
          <option value="all">全部</option>
          <option value="online">已上线</option>
          <option value="paused">已暂停</option>
          <option value="offline">未上线</option>
        </select>
      </div>
      <div class="flex flex-1 items-center gap-2">
        <input
          v-model="searchTerm"
          class="h-8 w-full max-w-md rounded border-slate-300 bg-white px-3 text-sm"
          placeholder="搜索流程名称或负责人..."
        >
      </div>
      <div class="flex items-center gap-1.5">
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">
          <span>↻</span> 刷新
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">
          <span>⤓</span> 导出
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded bg-slate-800 px-3 text-xs font-semibold text-white transition hover:bg-slate-900">
          <span>＋</span> 新建流程
        </button>
      </div>
    </div>

    <!-- table -->
    <div class="overflow-hidden rounded-md border border-slate-200 bg-white">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-200 bg-slate-50 text-left">
            <th class="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">流程名称</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">项目</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">负责人</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">调度</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">最近运行</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">运行时间</th>
            <th class="px-3 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wider text-slate-500">耗时</th>
            <th class="px-3 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wider text-slate-500">7 日成功率</th>
            <th class="px-4 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wider text-slate-500">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="wf in filtered" :key="wf.id"
            class="cursor-pointer border-b border-slate-100 transition last:border-0 hover:bg-slate-50/70"
            @click="emit('open-dag', wf.id)"
          >
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-slate-800">{{ wf.name }}</span>
                <span v-if="wf.last_run_status === 'running'" class="inline-flex items-center gap-1 rounded-full bg-sky-50 px-1.5 py-0.5 text-[10px] font-bold text-sky-700 ring-1 ring-inset ring-sky-200">
                  <span class="h-1.5 w-1.5 rounded-full bg-sky-500 animate-pulse"></span>
                  运行中
                </span>
              </div>
              <div class="mt-0.5 text-[11px] text-slate-400 font-mono">{{ wf.id }}</div>
            </td>
            <td class="px-3 py-3 text-slate-600">{{ projectName(wf.project) }}</td>
            <td class="px-3 py-3 text-slate-600">{{ wf.owner }}</td>
            <td class="px-3 py-3">
              <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="scheduleStatusMeta[wf.schedule_status].pill">
                {{ scheduleStatusMeta[wf.schedule_status].label }}
              </span>
              <div class="mt-0.5 text-[11px] text-slate-400 font-mono">{{ wf.schedule_text }}</div>
            </td>
            <td class="px-3 py-3">
              <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="statusMeta[wf.last_run_status].pill">
                <span class="h-1.5 w-1.5 rounded-full" :class="statusMeta[wf.last_run_status].dot"></span>
                {{ statusMeta[wf.last_run_status].label }}
              </span>
            </td>
            <td class="px-3 py-3 text-slate-600 font-mono text-[12px]">{{ wf.last_run_at }}</td>
            <td class="px-3 py-3 text-right text-slate-700 font-mono text-[12px]">{{ wf.last_duration }}</td>
            <td class="px-3 py-3 text-right">
              <span class="font-semibold font-mono text-[13px]" :class="successRateClass(wf.success_rate_7d)">
                {{ successRatePct(wf.success_rate_7d) }}
              </span>
            </td>
            <td class="px-4 py-3 text-right" @click.stop>
              <div class="flex items-center justify-end gap-0.5">
                <button class="rounded p-1.5 text-slate-500 transition hover:bg-emerald-50 hover:text-emerald-700" title="立即运行">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path d="M6 4l10 6-10 6V4z"/></svg>
                </button>
                <button v-if="wf.schedule_status === 'online'" class="rounded p-1.5 text-slate-500 transition hover:bg-amber-50 hover:text-amber-700" title="暂停调度">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path d="M6 4h3v12H6zM11 4h3v12h-3z"/></svg>
                </button>
                <button v-else class="rounded p-1.5 text-slate-500 transition hover:bg-emerald-50 hover:text-emerald-700" title="恢复调度">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path d="M5 5l10 5-10 5V5z"/></svg>
                </button>
                <button class="rounded p-1.5 text-slate-500 transition hover:bg-slate-100 hover:text-slate-700" title="查看实例" @click="emit('open-runs', wf.id)">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 4h14M3 10h14M3 16h14"/></svg>
                </button>
                <button class="rounded p-1.5 text-slate-500 transition hover:bg-slate-100 hover:text-slate-700" title="编辑">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 4l2 2-9 9-3 1 1-3 9-9z"/></svg>
                </button>
                <button class="rounded p-1.5 text-slate-500 transition hover:bg-rose-50 hover:text-rose-700" title="删除">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 7h10M8 7V4h4v3M6 7l1 10h6l1-10"/></svg>
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!filtered.length">
            <td colspan="9" class="py-16 text-center">
              <div class="text-slate-400">
                <svg class="mx-auto h-10 w-10" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 5h14v10H3zM7 9h6"/></svg>
                <p class="mt-2 text-sm">没有匹配的流程</p>
                <p class="mt-0.5 text-[11px]">尝试调整筛选条件或新建流程</p>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- ops insights row (low priority info) -->
    <div class="grid grid-cols-1 gap-3 lg:grid-cols-3">
      <div class="rounded-md border border-slate-200 bg-white p-4">
        <h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">最近失败作业</h3>
        <ul class="space-y-2 text-sm">
          <li v-for="item in opsStats.recent_failures" :key="item.workflow_id" class="flex items-center justify-between gap-2">
            <button class="truncate text-left text-slate-700 transition hover:text-rose-700" @click="emit('open-runs', item.workflow_id)">{{ item.name }}</button>
            <span class="shrink-0 font-mono text-[11px] text-slate-400">{{ item.at }} · {{ item.failed_node }}</span>
          </li>
        </ul>
      </div>
      <div class="rounded-md border border-slate-200 bg-white p-4">
        <h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">耗时排行（平均）</h3>
        <ul class="space-y-2 text-sm">
          <li v-for="item in opsStats.top_slow" :key="item.workflow_id" class="flex items-center justify-between gap-2">
            <button class="truncate text-left text-slate-700 transition hover:text-slate-900" @click="emit('open-dag', item.workflow_id)">{{ item.name }}</button>
            <span class="shrink-0 font-mono text-[11px] text-slate-500">{{ item.avg }}</span>
          </li>
        </ul>
      </div>
      <div class="rounded-md border border-slate-200 bg-white p-4">
        <h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">资源队列占用</h3>
        <ul class="space-y-2.5">
          <li v-for="q in opsStats.queue_usage" :key="q.name">
            <div class="flex items-center justify-between text-sm">
              <span class="font-mono text-[12px] text-slate-700">{{ q.name }}</span>
              <span class="font-mono text-[11px] text-slate-500">{{ q.running }} / {{ q.capacity }}</span>
            </div>
            <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                class="h-full"
                :class="q.running / q.capacity > 0.8 ? 'bg-rose-400' : (q.running / q.capacity > 0.5 ? 'bg-amber-400' : 'bg-emerald-400')"
                :style="{ width: `${Math.min(100, q.running / q.capacity * 100)}%` }"
              ></div>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>
