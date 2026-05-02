<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { healthMeta, workflowHealth } from '../../mock/workflow_meta'

const emit = defineEmits(['open-detail', 'run'])
const { state, allWorkflowRuns, loadAllWorkflowRuns } = inject('app')

onMounted(() => { loadAllWorkflowRuns() })

const ownerFilter = ref('all')
const healthFilter = ref('all')
const searchTerm = ref('')

// 现在元数据（owner / tags / schedule_cron）来自后端 Workflow 模型，
// 不再用 mock 模板插值。健康度和 24 小时运行次数仍然由运行历史推导。
const enriched = computed(() => state.workflows.map((wf) => {
  const wfRuns = allWorkflowRuns.value.filter((r) => r.workflow_id === wf.id)
  const latestRun = wfRuns[0] || null
  const success7d = wfRuns.slice(0, 7)
  const successCount = success7d.filter((r) => r.status === 'success').length
  return {
    ...wf,
    owner: wf.owner || '—',
    tags: Array.isArray(wf.tags) ? wf.tags : [],
    schedule_text: wf.schedule_cron || '手动触发',
    has_schedule: Boolean(wf.schedule_cron),
    description: wf.description || '',
    latest_run: latestRun,
    health: workflowHealth(wf, latestRun),
    last_run_status: latestRun?.status || 'none',
    last_run_at: latestRun?.started_at || '',
    last_duration: latestRun ? `${latestRun.elapsed_seconds}s` : '—',
    success_rate_7d: success7d.length ? successCount / success7d.length : null,
    runs_24h: wfRuns.filter((r) => r.started_at && r.started_at >= new Date(Date.now() - 86400_000).toISOString().slice(0, 19)).length,
  }
}))

// 真实下拉来源：所有不重复的 owner / tag。
const ownerOptions = computed(() => {
  const set = new Set()
  for (const wf of enriched.value) if (wf.owner && wf.owner !== '—') set.add(wf.owner)
  return [...set].sort()
})

const filtered = computed(() => enriched.value.filter((wf) => {
  if (ownerFilter.value !== 'all' && wf.owner !== ownerFilter.value) return false
  if (healthFilter.value !== 'all' && wf.health !== healthFilter.value) return false
  if (searchTerm.value) {
    const term = searchTerm.value.toLowerCase()
    const haystack = `${wf.name} ${wf.description} ${wf.tags.join(' ')} ${wf.owner}`.toLowerCase()
    if (!haystack.includes(term)) return false
  }
  return true
}))

// 顶部统计带：从所有 enriched 工作流和最近运行里推导。
const stats = computed(() => {
  const today = new Date().toISOString().slice(0, 10)
  const allRunsToday = allWorkflowRuns.value.filter((r) => (r.started_at || '').startsWith(today))
  const successToday = allRunsToday.filter((r) => r.status === 'success').length
  return {
    healthy:    enriched.value.filter((wf) => wf.health === 'healthy').length,
    failing:    enriched.value.filter((wf) => wf.health === 'failing').length,
    running:    enriched.value.filter((wf) => wf.health === 'running').length,
    runs_today: allRunsToday.length,
    success_rate: allRunsToday.length ? successToday / allRunsToday.length : null,
  }
})

const successRateClass = (rate) => {
  if (rate === null) return 'text-slate-400'
  if (rate >= 0.95) return 'text-emerald-600'
  if (rate >= 0.85) return 'text-amber-600'
  return 'text-rose-600'
}
const successRateText = (rate) => rate === null ? '—' : `${Math.round(rate * 100)}%`
</script>

<template>
  <div class="space-y-3">
    <!-- 统计带 -->
    <div class="grid grid-cols-2 gap-2 md:grid-cols-5">
      <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">健康作业</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-emerald-600">{{ stats.healthy }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">最近运行成功</p>
      </div>
      <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">失败作业</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-rose-600">{{ stats.failing }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">最近运行失败</p>
      </div>
      <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">运行中</p>
        <p class="mt-1 flex items-center gap-2 text-2xl font-bold tabular-nums text-blue-600">
          <span class="h-2 w-2 rounded-full bg-blue-500 animate-pulse"></span>{{ stats.running }}
        </p>
        <p class="mt-0.5 text-[11px] text-slate-500">实时</p>
      </div>
      <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">今日运行</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-slate-800">{{ stats.runs_today }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">总次数</p>
      </div>
      <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">平均成功率</p>
        <p class="mt-1 text-2xl font-bold tabular-nums" :class="successRateClass(stats.success_rate)">{{ successRateText(stats.success_rate) }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">今日</p>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
      <div class="flex items-center gap-1.5 text-xs">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">负责人</span>
        <select v-model="ownerFilter" class="h-7 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-700 focus:border-blue-400 focus:outline-none">
          <option value="all">全部</option>
          <option v-for="name in ownerOptions" :key="name" :value="name">{{ name }}</option>
        </select>
      </div>
      <div class="flex items-center gap-1.5 text-xs">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">状态</span>
        <button
          v-for="key in ['all', 'healthy', 'failing', 'running', 'none']" :key="key"
          class="rounded-lg border px-2 py-0.5 text-[11px] transition"
          :class="healthFilter === key ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700'"
          @click="healthFilter = key"
        >{{ key === 'all' ? '全部' : healthMeta[key].label }}</button>
      </div>
      <input
        v-model="searchTerm"
        placeholder="搜索作业流名称 / 描述 / 标签..."
        class="h-7 w-56 rounded-lg border border-slate-200 bg-white px-2 text-[12px] text-slate-700 placeholder:text-slate-400 focus:border-blue-400 focus:outline-none"
      >
      <div class="ml-auto flex items-center gap-1.5">
        <button class="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50" @click="loadAllWorkflowRuns">
          ↻ 刷新
        </button>
        <button class="inline-flex h-7 items-center gap-1.5 rounded-lg bg-blue-600 px-2.5 text-[11px] font-semibold text-white transition hover:bg-blue-700" @click="emit('open-detail', 'new')">
          ＋ 新建作业流
        </button>
      </div>
    </div>

    <!-- 列表 -->
    <div class="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <table class="w-full text-sm">
        <thead class="border-b border-slate-200 bg-slate-50">
          <tr class="text-left">
            <th class="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">作业流</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">负责人</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">健康度</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">最近运行</th>
            <th class="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">调度</th>
            <th class="px-3 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wider text-slate-500">7 日成功率</th>
            <th class="px-4 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wider text-slate-500">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="wf in filtered" :key="wf.id"
              class="cursor-pointer border-b border-slate-100 transition last:border-0 hover:bg-slate-50/70"
              @click="emit('open-detail', wf.id)">
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-slate-800">{{ wf.name }}</span>
                <span v-for="tag in wf.tags.slice(0, 2)" :key="tag" class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-mono text-slate-600">{{ tag }}</span>
              </div>
              <p v-if="wf.description" class="mt-0.5 line-clamp-1 max-w-md text-[11px] text-slate-500">{{ wf.description }}</p>
              <p v-else class="mt-0.5 text-[11px] text-slate-300">无描述</p>
            </td>
            <td class="px-3 py-3 text-slate-600">{{ wf.owner }}</td>
            <td class="px-3 py-3">
              <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="healthMeta[wf.health].pill">
                <span class="h-1.5 w-1.5 rounded-full" :class="healthMeta[wf.health].dot"></span>
                {{ healthMeta[wf.health].label }}
              </span>
            </td>
            <td class="px-3 py-3">
              <p class="font-mono text-[12px] text-slate-700">{{ wf.last_run_at ? wf.last_run_at.slice(5) : '—' }}</p>
              <p class="font-mono text-[10.5px] text-slate-500">{{ wf.last_duration }}</p>
            </td>
            <td class="px-3 py-3">
              <p v-if="wf.has_schedule" class="font-mono text-[12px] text-slate-700">{{ wf.schedule_text }}</p>
              <p v-else class="text-[11.5px] text-slate-400">手动触发</p>
            </td>
            <td class="px-3 py-3 text-right">
              <span class="font-semibold font-mono text-[13px] tabular-nums" :class="successRateClass(wf.success_rate_7d)">{{ successRateText(wf.success_rate_7d) }}</span>
            </td>
            <td class="px-4 py-3 text-right" @click.stop>
              <div class="flex items-center justify-end gap-0.5">
                <button class="rounded p-1.5 text-slate-500 transition hover:bg-emerald-50 hover:text-emerald-700" title="立即运行（后台执行）" @click="emit('run', wf.id)">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path d="M6 4l10 6-10 6V4z"/></svg>
                </button>
                <button class="rounded p-1.5 text-slate-500 transition hover:bg-blue-50 hover:text-blue-700" title="查看详情" @click="emit('open-detail', wf.id)">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 4h14M3 10h14M3 16h14"/></svg>
                </button>
                <button class="rounded p-1.5 text-slate-500 transition hover:bg-slate-100 hover:text-slate-700" title="编辑（同详情页）" @click="emit('open-detail', wf.id)">
                  <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 4l2 2-9 9-3 1 1-3 9-9z"/></svg>
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!filtered.length">
            <td colspan="7" class="py-16 text-center">
              <p class="text-sm text-slate-400">{{ state.workflows.length ? '没有匹配的作业流' : '还没有作业流，请先在「配置」中创建' }}</p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
