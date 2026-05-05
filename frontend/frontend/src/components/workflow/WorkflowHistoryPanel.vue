<script setup>
import { ref } from 'vue'
import { storeToRefs } from 'pinia'
import { apiGet } from '../../api'
import { nodeStatusMeta } from '../../mock/workflow_meta'
import { useWorkflowStore } from '../../stores/workflow'

const emit = defineEmits(['open-run'])

// 共享状态从 store 拿；列表数据 workflowRunHistory 由 store 在 selectWorkflow /
// 跑完一次 run 时刷新。
const workflowStore = useWorkflowStore()
const { currentWorkflow, workflowRunHistory } = storeToRefs(workflowStore)
const { runWorkflowAsyncWith } = workflowStore


// --- 行展开 + mini gantt ---

const expandedHistoryRun = ref('')          // 当前展开的 run_id（每次只展一行）
const historyDetailCache = ref({})          // run_id → 完整 run 详情
const historyDetailLoading = ref({})        // run_id → bool

const toggleHistoryExpand = async (runId) => {
  if (expandedHistoryRun.value === runId) {
    expandedHistoryRun.value = ''
    return
  }
  expandedHistoryRun.value = runId
  if (historyDetailCache.value[runId]) return
  historyDetailLoading.value[runId] = true
  try {
    historyDetailCache.value[runId] = await apiGet(`/api/workflow-runs/${runId}`)
  } catch (_) {
    historyDetailCache.value[runId] = null
  } finally {
    historyDetailLoading.value[runId] = false
  }
}


// --- 状态徽章配色 + 部分跳过的特殊态 ---

const historyRunStatusDisplay = (run) => {
  const status = run.status
  const skipped = run.node_status_counts?.skipped || 0
  if (status === 'success' && skipped > 0) {
    return { label: `部分跳过 (${skipped})`, pillClass: 'bg-amber-50 text-amber-700 ring-amber-200', dotClass: 'bg-amber-500' }
  }
  if (status === 'success') {
    return { label: '成功', pillClass: 'bg-emerald-50 text-emerald-700 ring-emerald-200', dotClass: 'bg-emerald-500' }
  }
  if (status === 'failed') {
    return { label: '失败', pillClass: 'bg-rose-50 text-rose-700 ring-rose-200', dotClass: 'bg-rose-500' }
  }
  if (status === 'cancelled') {
    return { label: '已取消', pillClass: 'bg-slate-100 text-slate-600 ring-slate-200', dotClass: 'bg-slate-400' }
  }
  return { label: status || '—', pillClass: 'bg-slate-100 text-slate-600 ring-slate-200', dotClass: 'bg-slate-400' }
}


// --- 复用变量重跑：剥掉内置时间变量，避免冻结到历史那天 ---

const REUSABLE_BUILTIN_KEYS = new Set(['today', 'now', 'year', 'month', 'day'])
const reusableVariables = (vars) => {
  if (!vars || typeof vars !== 'object') return {}
  const out = {}
  for (const [k, v] of Object.entries(vars)) {
    if (!REUSABLE_BUILTIN_KEYS.has(k)) out[k] = v
  }
  return out
}
const reuseAndRerun = (run, detail) => {
  if (!currentWorkflow.value?.id) return
  const vars = reusableVariables(detail.variables || {})
  runWorkflowAsyncWith(currentWorkflow.value.id, vars)
}


// --- mini gantt 数据：把 detail 拍平成 (node, offsetSec, duration) ---
// 同 WorkflowRunView 的 ganttData，但只展一行 detail，单独不复用 view 级的。

const historyGantt = (detail) => {
  if (!detail || !Array.isArray(detail.nodes)) return { steps: [], totalSeconds: 1 }
  const parseTs = (s) => {
    if (!s) return null
    const t = Date.parse(s.includes('T') ? s : s.replace(' ', 'T'))
    return isFinite(t) ? t : null
  }
  const startTs = parseTs(detail.started_at)
  let total = detail.elapsed_seconds || 1
  const steps = detail.nodes.map((n) => {
    const offsetSec = n.started_at && startTs ? Math.max(0, (parseTs(n.started_at) - startTs) / 1000) : 0
    const duration = n.elapsed_seconds || 0
    if (offsetSec + duration > total) total = offsetSec + duration
    return { node: n, offsetSec, duration }
  })
  return { steps, totalSeconds: Math.max(total, 1) }
}
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead class="border-b border-slate-200 bg-slate-50/60">
        <tr class="text-left">
          <th class="w-8"></th>
          <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">状态</th>
          <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">运行 ID</th>
          <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">开始时间</th>
          <th class="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-slate-500">耗时</th>
          <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">节点</th>
          <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">错误</th>
          <th class="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-slate-500">操作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="run in workflowRunHistory" :key="run.run_id">
          <tr class="cursor-pointer border-b border-slate-100 last:border-0 transition hover:bg-slate-50/70"
              :class="expandedHistoryRun === run.run_id ? 'bg-blue-50/30' : ''"
              @click="toggleHistoryExpand(run.run_id)">
            <td class="px-2 py-2.5 text-center font-mono text-[10px] text-slate-400">
              {{ expandedHistoryRun === run.run_id ? '▾' : '▸' }}
            </td>
            <td class="px-3 py-2.5">
              <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset"
                    :class="historyRunStatusDisplay(run).pillClass">
                <span class="h-1.5 w-1.5 rounded-full" :class="historyRunStatusDisplay(run).dotClass"></span>
                {{ historyRunStatusDisplay(run).label }}
              </span>
            </td>
            <td class="px-3 py-2.5 font-mono text-[11.5px] text-slate-600">{{ run.run_id.slice(0, 12) }}</td>
            <td class="px-3 py-2.5 font-mono text-[11.5px] text-slate-700">{{ run.started_at }}</td>
            <td class="px-3 py-2.5 text-right font-mono text-[11.5px] text-slate-700">{{ run.elapsed_seconds }}s</td>
            <td class="px-3 py-2.5 text-[11px]">
              <span class="text-emerald-600 font-mono">✓{{ run.node_status_counts.success || 0 }}</span>
              <span v-if="run.node_status_counts.failed" class="ml-2 text-rose-600 font-mono">✕{{ run.node_status_counts.failed }}</span>
              <span v-if="run.node_status_counts.skipped" class="ml-2 text-slate-500 font-mono">⊘{{ run.node_status_counts.skipped }}</span>
            </td>
            <td class="px-3 py-2.5 text-[11.5px] text-rose-600">{{ run.error || '' }}</td>
            <td class="px-3 py-2.5 text-right" @click.stop>
              <div class="inline-flex items-center gap-1">
                <button class="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-[10px] font-bold text-emerald-700 transition hover:bg-emerald-100"
                        title="按当前作业流配置 + 默认变量重跑（不复用此次的运行变量）"
                        @click="runWorkflowAsyncWith(currentWorkflow?.id)">
                  ↻ 重跑
                </button>
                <button class="rounded bg-slate-700 px-2 py-1 text-[10px] font-bold text-white transition hover:bg-blue-600" @click="emit('open-run', run.run_id)">查看 →</button>
              </div>
            </td>
          </tr>
          <!-- 展开行：mini gantt -->
          <tr v-if="expandedHistoryRun === run.run_id">
            <td colspan="8" class="border-b border-slate-100 bg-slate-50/40 px-4 py-3">
              <div v-if="historyDetailLoading[run.run_id]" class="text-center text-[11.5px] text-slate-400">加载中...</div>
              <div v-else-if="!historyDetailCache[run.run_id]" class="text-center text-[11.5px] text-rose-500">加载失败 — 请重试</div>
              <div v-else>
                <div class="mb-2 flex items-center justify-between">
                  <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">
                    节点时间线 · 共 {{ Math.round(historyGantt(historyDetailCache[run.run_id]).totalSeconds) }} 秒
                  </span>
                  <button v-if="Object.keys(reusableVariables(historyDetailCache[run.run_id].variables)).length"
                          class="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10.5px] font-semibold text-blue-700 transition hover:bg-blue-100"
                          :title="`复用本次的变量（${Object.keys(reusableVariables(historyDetailCache[run.run_id].variables)).join(', ')}）重跑；today/now 等内置变量不复用，每次跑重算`"
                          @click="reuseAndRerun(run, historyDetailCache[run.run_id])">
                    ↻ 复用此次变量重跑
                  </button>
                </div>
                <div class="space-y-1">
                  <div v-for="step in historyGantt(historyDetailCache[run.run_id]).steps" :key="step.node.node_id"
                       class="grid grid-cols-[160px_minmax(0,1fr)_60px] items-center gap-3 text-[11px]">
                    <span class="flex items-center gap-1.5 truncate">
                      <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="nodeStatusMeta[step.node.status]?.dot || 'bg-slate-300'"></span>
                      <span class="truncate font-medium text-slate-700">{{ step.node.name || step.node.node_id }}</span>
                      <span class="font-mono text-[9.5px] text-slate-400">· {{ step.node.type }}</span>
                    </span>
                    <span class="relative h-2 rounded-full bg-slate-100">
                      <span class="absolute top-0 h-2 rounded-full"
                            :class="nodeStatusMeta[step.node.status]?.bar || 'bg-slate-300'"
                            :style="{
                              left: (step.offsetSec / historyGantt(historyDetailCache[run.run_id]).totalSeconds * 100) + '%',
                              width: Math.max(0.5, step.duration / historyGantt(historyDetailCache[run.run_id]).totalSeconds * 100) + '%',
                            }"></span>
                    </span>
                    <span class="text-right font-mono tabular-nums text-[10.5px] text-slate-500">{{ step.duration }}s</span>
                  </div>
                </div>
              </div>
            </td>
          </tr>
        </template>
        <tr v-if="!workflowRunHistory.length"><td colspan="8" class="py-8 text-center text-[12.5px] text-slate-400">还没有历史运行 — 顶部点「执行」或「后台执行」跑一次，结果会留在这里</td></tr>
      </tbody>
    </table>
  </div>
</template>
