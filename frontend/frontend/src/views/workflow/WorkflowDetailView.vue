<script setup>
import { computed, defineAsyncComponent, inject, ref, watch } from 'vue'
import { apiGet } from '../../api'
import { nodeStatusMeta, layoutDAG, workflowHealth, synthesizeEvents, parameterTypeMeta as _ptm, resolveAllParameters } from '../../mock/workflow_meta'
import WorkflowCompareNodeEditor     from '../../components/workflow/WorkflowCompareNodeEditor.vue'
import WorkflowParamsNodeEditor      from '../../components/workflow/WorkflowParamsNodeEditor.vue'
import WorkflowLineageNodeEditor     from '../../components/workflow/WorkflowLineageNodeEditor.vue'
import WorkflowExcelExportNodeEditor from '../../components/workflow/WorkflowExcelExportNodeEditor.vue'

const parameterTypeMeta = _ptm

const SqlEditor = defineAsyncComponent(() => import('../../components/SqlEditor.vue'))

const emit = defineEmits(['back', 'open-run'])

// 注：节点编辑器（params / compare / lineage / excel_export）已抽成子组件，
// 它们各自从 inject('app') 拿需要的全局方法（addParameter / addExportSheet
// 等），所以这里不再解构那些 helper。父组件保留 addWorkflowNode 等还在用的。
const {
  state, workflowDraft, selectedWorkflowId, currentWorkflow, isSavedWorkflow,
  workflowResult, workflowAsyncJob, workflowAsyncStatus, workflowRunHistory,
  allWorkflowRuns, loadAllWorkflowRuns,
  saveWorkflow, deleteWorkflow,
  runWorkflow, runWorkflowAsync, runWorkflowAsyncWith, cancelWorkflowAsync,
  addWorkflowNode, removeWorkflowNode, moveWorkflowNode,
  loadWorkflowRunDetail,
} = inject('app')

// --- 运行历史 tab：展开行加 mini gantt ---
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

// 历史运行行的状态显示：success + 有 skipped 节点 → "部分跳过"，色调改琥珀。
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

// 复用历史变量重跑时，剥掉内置变量（today/now/year/month/day）—— 否则
// 内置时间会被冻结到历史那天，破坏 relative_date 这类参数的"每次跑都重算"语义。
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
  const vars = reusableVariables(detail.variables || {})
  runWorkflowAsyncWith(currentWorkflow.value.id, vars)
}

// 把 detail 拍平成 gantt 步骤（offset / duration / status），同 WorkflowRunView。
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

const activeTab = ref('history')   // history / events / lineage / config
const selectedNodeId = ref('')

// 新建态：自动落到「节点配置」tab，引导用户开始编辑
watch(selectedWorkflowId, (id) => {
  if (id === 'new') activeTab.value = 'config'
}, { immediate: true })

// 运行参数：从作业流里所有 type=params 节点的 config.parameters 收集。
// 没有 params 节点 → 空列表，UI 显示"还没有参数定义"占位。
const realParameters = computed(() => {
  const out = []
  for (const node of workflowDraft.nodes || []) {
    if (node.type !== 'params') continue
    for (const p of node.parameters || []) {
      if (p?.name) out.push(p)
    }
  }
  return out
})
const displayParameters = computed(() => realParameters.value)

// 参数解析：把每个参数定义解析成下次运行将使用的具体值（预览用）。
const resolvedParams = computed(() => resolveAllParameters(displayParameters.value))

const compareTaskOptions = computed(() => state.tasks.map((task) => ({ id: task.id, name: task.name })))

// 最近一次 run（用于 DAG canvas 上叠加节点状态）
const latestRun = computed(() => workflowResult.value || null)
const health = computed(() => workflowHealth(currentWorkflow.value, workflowRunHistory.value[0] || null))

const nodeStatusByid = computed(() => {
  const map = {}
  if (latestRun.value) {
    for (const n of latestRun.value.nodes || []) map[n.node_id] = n
  }
  return map
})

// DAG 自动布局
const layout = computed(() => {
  const nodes = workflowDraft.nodes.map((n) => ({ id: n.id, name: n.name, type: n.type, depends_on: n.depends_on || [] }))
  return layoutDAG(nodes, { nodeW: 220, nodeH: 84, gapX: 80, gapY: 28, padX: 40, padY: 40 })
})

const positionedById = computed(() => {
  const map = {}
  for (const n of layout.value.positioned) map[n.id] = n
  return map
})

const allEdges = computed(() => {
  const out = []
  for (const n of workflowDraft.nodes) {
    for (const dep of n.depends_on || []) out.push({ source: dep, target: n.id })
  }
  return out
})

const edgePath = (edge) => {
  const s = positionedById.value[edge.source]
  const t = positionedById.value[edge.target]
  if (!s || !t) return ''
  const sx = s.x + 220
  const sy = s.y + 84 / 2
  const tx = t.x
  const ty = t.y + 84 / 2
  const dx = Math.max(40, (tx - sx) * 0.5)
  return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`
}

const edgeHighlighted = (edge) =>
  selectedNodeId.value && (edge.source === selectedNodeId.value || edge.target === selectedNodeId.value)

const nodeStatus = (nodeId) => nodeStatusByid.value[nodeId]?.status || 'pending'

// DAG canvas 节点 box 描边色 —— 失败/跳过的节点要醒目，光看小圆点容易漏。
// selected 仍走蓝环；非 selected 时按状态着色。
const nodeBoxClass = (nodeId) => {
  if (selectedNodeId.value === nodeId) return 'border-blue-400 ring-2 ring-blue-200'
  const status = nodeStatus(nodeId)
  if (status === 'failed')   return 'border-rose-300 bg-rose-50/50'
  if (status === 'skipped')  return 'border-amber-300 bg-amber-50/40'
  if (status === 'running')  return 'border-blue-300 ring-1 ring-blue-100 animate-pulse'
  if (status === 'success')  return 'border-emerald-200'
  return 'border-slate-200'
}

// DAG canvas hover tooltip：显示完整 error / 时间 / 节点 id 等。
// 用 mouseenter/leave 切 hoveredNodeId；popover 绝对定位在节点旁，
// pointer-events-none 让鼠标穿透到节点本身（避免 hover 闪烁）。
const NODE_BOX_W = 220
const NODE_BOX_H = 84
const hoveredNodeId = ref('')
const hoveredNode = computed(() => layout.value.positioned.find((n) => n.id === hoveredNodeId.value) || null)
const hoveredStatus = computed(() => nodeStatusByid.value[hoveredNodeId.value] || null)
const hoveredHasInfo = computed(() => Boolean(hoveredStatus.value || hoveredNode.value))

// 计算 popover 位置：默认放节点右侧；右边放不下则放左侧；都放不下就上方。
const TOOLTIP_W = 300
const TOOLTIP_H = 160
const tooltipStyle = computed(() => {
  if (!hoveredNode.value) return { display: 'none' }
  const n = hoveredNode.value
  const placedRight = n.x + NODE_BOX_W + 12 + TOOLTIP_W <= layout.value.width
  const left = placedRight ? n.x + NODE_BOX_W + 12 : Math.max(8, n.x - TOOLTIP_W - 12)
  // 让 tooltip 顶部和节点对齐，超出底部时向上挤
  const top = Math.min(n.y, Math.max(8, layout.value.height - TOOLTIP_H - 8))
  return { left: left + 'px', top: top + 'px', width: TOOLTIP_W + 'px' }
})

const otherNodeIds = (currentId) => workflowDraft.nodes.map((n) => n.id).filter((id) => id && id !== currentId)

const tabs = [
  { id: 'history',  label: '运行历史' },
  { id: 'events',   label: '事件日志' },
  { id: 'lineage',  label: '依赖关系' },
  { id: 'config',   label: '节点配置' },
]

// 合成事件流（来自最近一次运行）
const recentEvents = computed(() => synthesizeEvents(latestRun.value))
const eventTypeMeta = {
  RUN_START:    { glyph: '▶', text: 'text-blue-600',    label: '运行开始' },
  RUN_SUCCESS:  { glyph: '✓', text: 'text-emerald-600', label: '运行成功' },
  RUN_FAILURE:  { glyph: '✕', text: 'text-rose-600',    label: '运行失败' },
  STEP_START:   { glyph: '·', text: 'text-slate-500',   label: '步骤开始' },
  STEP_SUCCESS: { glyph: '✓', text: 'text-emerald-600', label: '步骤完成' },
  STEP_FAILURE: { glyph: '✕', text: 'text-rose-600',    label: '步骤失败' },
  STEP_SKIPPED: { glyph: '⊘', text: 'text-slate-500',   label: '步骤跳过' },
}
const levelClass = (level) => ({ INFO: 'text-slate-700', WARN: 'text-amber-700', ERROR: 'text-rose-700' }[level] || 'text-slate-700')

watch(selectedWorkflowId, () => { selectedNodeId.value = '' })
</script>

<template>
  <div v-if="!currentWorkflow && selectedWorkflowId !== 'new'" class="rounded-xl border border-dashed border-slate-200 bg-white p-12 text-center text-sm text-slate-400">
    请先从「作业流总览」中选择一个作业流，或点击右上角「新建作业流」
  </div>

  <div v-else class="flex flex-col gap-3">
    <!-- 顶部 header：名称 + 健康度 + 调度 + 操作 -->
    <div class="rounded-xl border border-slate-200 bg-white shadow-sm">
      <div class="flex items-start justify-between gap-3 px-4 py-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-[11px] text-slate-500">
            <button class="text-slate-600 transition hover:text-blue-600" @click="emit('back')">← 作业流总览</button>
            <span v-if="selectedWorkflowId !== 'new'" class="text-slate-300">/</span>
            <span v-if="selectedWorkflowId !== 'new'" class="font-mono">{{ selectedWorkflowId.slice(0, 8) }}</span>
          </div>
          <div class="mt-1.5 flex flex-wrap items-center gap-2.5">
            <input v-if="selectedWorkflowId === 'new'"
                   v-model="workflowDraft.name"
                   placeholder="新建作业流名称..."
                   class="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xl font-bold text-slate-800 focus:border-blue-400 focus:outline-none">
            <h1 v-else class="text-xl font-bold text-slate-800">{{ currentWorkflow.name }}</h1>
            <span v-if="selectedWorkflowId === 'new'" class="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700 ring-1 ring-inset ring-blue-200">
              <span class="h-1.5 w-1.5 rounded-full bg-blue-500"></span>
              草稿（未保存）
            </span>
            <template v-else>
              <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="healthMeta[health].pill">
                <span class="h-1.5 w-1.5 rounded-full" :class="healthMeta[health].dot"></span>
                {{ healthMeta[health].label }}
              </span>
              <span v-if="latestRun" class="font-mono text-[11.5px] text-slate-500">
                最近运行：{{ latestRun.started_at?.slice(5) }} · {{ latestRun.elapsed_seconds }}s
              </span>
              <span v-if="workflowDraft.schedule_cron" class="font-mono text-[11.5px] text-slate-500" title="cron 表达式（仅展示，外部调度器读取）">
                ⏱ {{ workflowDraft.schedule_cron }}
              </span>
              <span v-else class="text-[11px] text-slate-400">手动触发</span>
              <span class="text-slate-300">·</span>
              <span class="text-[11.5px] text-slate-500">
                负责人 <span class="font-medium text-slate-700">{{ workflowDraft.owner || '—' }}</span>
              </span>
            </template>
          </div>
          <div v-if="selectedWorkflowId !== 'new'" class="mt-1.5 flex flex-wrap items-center gap-1">
            <span v-for="tag in workflowDraft.tags" :key="tag" class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ tag }}</span>
            <span v-if="!workflowDraft.tags?.length" class="text-[10.5px] text-slate-300">无标签</span>
          </div>
          <p v-else class="mt-1.5 text-[11.5px] text-slate-500">填写名称、在「节点配置」中添加节点，然后保存。保存后即可执行。</p>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!isSavedWorkflow" :title="!isSavedWorkflow ? '请先保存作业流' : '立即运行'" @click="runWorkflow">
            <span>▶</span> 立即运行
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!isSavedWorkflow" :title="!isSavedWorkflow ? '请先保存作业流' : '提交后台执行'" @click="runWorkflowAsync">
            后台执行
          </button>
          <button v-if="workflowAsyncJob && workflowAsyncStatus && !['success','failed','cancelled'].includes(workflowAsyncStatus.status)"
                  class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-2.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100"
                  @click="cancelWorkflowAsync">
            ▣ 取消
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50" @click="saveWorkflow">
            保存
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-rose-700 transition hover:border-rose-200 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!isSavedWorkflow" @click="deleteWorkflow">
            删除
          </button>
        </div>
      </div>
    </div>

    <!-- 主区域：左 DAG canvas + 右元数据 -->
    <div class="grid grid-cols-[minmax(0,1fr)_320px] gap-3">
      <!-- DAG canvas -->
      <div class="flex flex-col rounded-xl border border-slate-200 bg-white shadow-sm">
        <div class="flex items-center justify-between border-b border-slate-200 bg-slate-50/60 px-3 py-1.5">
          <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">DAG · 节点 {{ workflowDraft.nodes.length }} · 依赖 {{ allEdges.length }}</span>
          <div class="flex items-center gap-3 text-[10.5px] text-slate-500">
            <span v-for="(meta2, key) in nodeStatusMeta" :key="key" class="flex items-center gap-1">
              <span class="h-1.5 w-1.5 rounded-full" :class="meta2.dot"></span>{{ meta2.label }}
            </span>
          </div>
        </div>
        <div class="relative max-h-[520px] flex-1 overflow-auto">
          <div v-if="!workflowDraft.nodes.length" class="grid h-[280px] place-items-center">
            <div class="text-center">
              <p class="text-sm text-slate-400">还没有节点</p>
              <button class="mt-2 inline-flex h-7 items-center gap-1 rounded-lg bg-blue-600 px-2.5 text-xs font-semibold text-white transition hover:bg-blue-700" @click="activeTab = 'config'; addWorkflowNode()">+ 在「节点配置」中添加</button>
            </div>
          </div>
          <div v-else
               class="relative"
               :style="{
                 width: layout.width + 'px',
                 height: layout.height + 'px',
                 backgroundImage: 'radial-gradient(circle at 1px 1px, rgb(203 213 225 / 0.6) 1px, transparent 0)',
                 backgroundSize: '24px 24px',
                 minHeight: '280px',
               }">
            <svg class="pointer-events-none absolute inset-0" :width="layout.width" :height="layout.height" :viewBox="`0 0 ${layout.width} ${layout.height}`">
              <defs>
                <marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker>
                <marker id="wf-arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#2563eb"/></marker>
              </defs>
              <path v-for="(edge, idx) in allEdges" :key="idx"
                    :d="edgePath(edge)" fill="none"
                    :stroke="edgeHighlighted(edge) ? '#2563eb' : '#94a3b8'"
                    :stroke-width="edgeHighlighted(edge) ? 2 : 1.2"
                    :stroke-opacity="edgeHighlighted(edge) ? 0.9 : 0.55"
                    :marker-end="edgeHighlighted(edge) ? 'url(#wf-arrow-active)' : 'url(#wf-arrow)'" />
            </svg>
            <button v-for="n in layout.positioned" :key="n.id"
                    class="absolute flex flex-col gap-1 rounded-xl border bg-white px-3 py-2 text-left shadow-sm transition hover:shadow-md"
                    :class="nodeBoxClass(n.id)"
                    :style="{ left: n.x + 'px', top: n.y + 'px', width: '220px', height: '84px' }"
                    @click="selectedNodeId = n.id"
                    @mouseenter="hoveredNodeId = n.id"
                    @mouseleave="hoveredNodeId = ''">
              <div class="flex items-center gap-1.5">
                <span class="h-2 w-2 shrink-0 rounded-full" :class="nodeStatusMeta[nodeStatus(n.id)].dot"></span>
                <span class="truncate text-[12.5px] font-semibold text-slate-800">{{ n.name || n.id }}</span>
              </div>
              <div class="flex items-center gap-1.5 text-[10.5px]">
                <span class="rounded px-1 font-mono text-[9.5px] font-bold uppercase"
                      :class="{
                        'bg-sky-50 text-sky-700': n.type === 'params',
                        'bg-blue-50 text-blue-700': n.type === 'compare',
                        'bg-emerald-50 text-emerald-700': n.type === 'lineage',
                        'bg-amber-50 text-amber-700': n.type === 'excel_export',
                        'bg-purple-50 text-purple-700': n.type === 'http',
                      }">{{ n.type }}</span>
                <span class="font-mono text-slate-500">{{ n.id }}</span>
                <span v-if="nodeStatusByid[n.id]" class="ml-auto font-mono text-slate-500">{{ nodeStatusByid[n.id].elapsed_seconds }}s</span>
              </div>
              <span v-if="nodeStatusByid[n.id]?.error" class="line-clamp-1 text-[10px] text-rose-600">{{ nodeStatusByid[n.id].error }}</span>
            </button>

            <!-- hover tooltip（覆盖节点旁，pointer-events-none 不挡鼠标）-->
            <div v-if="hoveredNode && hoveredHasInfo"
                 class="pointer-events-none absolute z-20 rounded-lg border border-slate-300 bg-white p-3 shadow-xl"
                 :style="tooltipStyle">
              <div class="flex items-center gap-2 border-b border-slate-100 pb-2">
                <span class="h-2 w-2 rounded-full" :class="nodeStatusMeta[nodeStatus(hoveredNode.id)].dot"></span>
                <span class="text-[12px] font-bold text-slate-800">{{ hoveredNode.name || hoveredNode.id }}</span>
                <span class="rounded px-1 font-mono text-[9.5px] font-bold uppercase"
                      :class="{
                        'bg-sky-50 text-sky-700': hoveredNode.type === 'params',
                        'bg-blue-50 text-blue-700': hoveredNode.type === 'compare',
                        'bg-emerald-50 text-emerald-700': hoveredNode.type === 'lineage',
                        'bg-amber-50 text-amber-700': hoveredNode.type === 'excel_export',
                        'bg-purple-50 text-purple-700': hoveredNode.type === 'http',
                      }">{{ hoveredNode.type }}</span>
                <span class="ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ring-inset" :class="nodeStatusMeta[nodeStatus(hoveredNode.id)].pill">
                  {{ nodeStatusMeta[nodeStatus(hoveredNode.id)].label }}
                </span>
              </div>
              <dl v-if="hoveredStatus" class="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                <div><dt class="text-slate-400">开始</dt><dd class="font-mono text-slate-700">{{ hoveredStatus.started_at?.slice(11) || '—' }}</dd></div>
                <div><dt class="text-slate-400">耗时</dt><dd class="font-mono text-slate-700">{{ hoveredStatus.elapsed_seconds }}s</dd></div>
                <div><dt class="text-slate-400">node_id</dt><dd class="font-mono text-slate-700">{{ hoveredNode.id }}</dd></div>
                <div v-if="(hoveredNode.depends_on || []).length"><dt class="text-slate-400">depends_on</dt><dd class="font-mono text-slate-700">{{ (hoveredNode.depends_on || []).join(', ') }}</dd></div>
              </dl>
              <p v-else class="mt-2 text-[11px] text-slate-400">未运行 — DAG 上展示的是上次运行的状态</p>
              <div v-if="hoveredStatus?.error" class="mt-2 rounded border border-rose-200 bg-rose-50/60 px-2 py-1.5 text-[11px] leading-relaxed text-rose-800">
                <p class="text-[10px] font-bold uppercase tracking-wider text-rose-700">错误</p>
                <pre class="mt-1 whitespace-pre-wrap font-mono text-[10.5px]">{{ hoveredStatus.error }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 元数据侧栏 -->
      <aside class="flex flex-col gap-3">
        <!-- 运行参数：参数驱动作业流的核心信息 -->
        <div class="rounded-xl border border-slate-200 bg-white shadow-sm">
          <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
            <p class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">运行参数</p>
            <span class="text-[10.5px] text-slate-500">{{ displayParameters.length }} 个</span>
          </div>
          <ul class="divide-y divide-slate-100">
            <li v-for="param in displayParameters" :key="param.name" class="px-3 py-2.5">
              <div class="flex items-center gap-1.5">
                <span class="rounded px-1 py-0.5 text-[9.5px] font-bold uppercase ring-1 ring-inset" :class="parameterTypeMeta[param.type].accent">{{ parameterTypeMeta[param.type].glyph }} {{ parameterTypeMeta[param.type].label }}</span>
                <span class="font-mono text-[12px] font-semibold text-slate-800">{{ param.name }}</span>
                <span v-if="param.required" class="ml-auto text-[10px] font-semibold text-rose-600">必填</span>
                <span v-else class="ml-auto text-[10px] text-slate-400">可选</span>
              </div>
              <p class="mt-0.5 text-[11px] text-slate-500">{{ param.description }}</p>
              <div class="mt-1 flex items-baseline gap-1.5">
                <span class="text-[10px] uppercase tracking-wider text-slate-400">解析后</span>
                <span v-if="resolvedParams[param.name].kind === 'list'" class="font-mono text-[11px]">
                  <span v-for="(v, i) in resolvedParams[param.name].value" :key="i" class="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{{ v }}</span>
                </span>
                <span v-else-if="resolvedParams[param.name].kind === 'pending'" class="rounded bg-emerald-50 px-1.5 py-0.5 font-mono text-[11px] text-emerald-700 ring-1 ring-emerald-200">{{ resolvedParams[param.name].value }}</span>
                <span v-else-if="resolvedParams[param.name].kind === 'derived'" class="rounded bg-blue-50 px-1.5 py-0.5 font-mono text-[11px] text-blue-700 ring-1 ring-blue-200">{{ resolvedParams[param.name].value }}</span>
                <span v-else class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-700">{{ resolvedParams[param.name].value }}</span>
              </div>
            </li>
            <li v-if="!displayParameters.length" class="px-3 py-3 text-center text-[11px] text-slate-400">还没有参数定义 — 在画布添加 <code class="rounded bg-slate-100 px-1 font-mono">params</code> 节点，或保存后右上角直接添加</li>
          </ul>
          <div class="border-t border-slate-100 px-3 py-2 text-[10.5px] text-slate-500">
            可在 SQL / 文件名 / Sheet 名等位置用 <code class="rounded bg-slate-100 px-1 font-mono">${name}</code> 引用
          </div>
        </div>

        <!-- 元数据：可编辑，落到 Workflow 模型；保存后即生效 -->
        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">元数据</p>
          <div class="space-y-2">
            <label class="block">
              <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">描述</span>
              <textarea v-model="workflowDraft.description" rows="3" placeholder="一句话说清这个作业流的目的"
                        class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-[12px] text-slate-700"></textarea>
            </label>
            <div class="grid grid-cols-2 gap-2">
              <label class="block">
                <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">项目</span>
                <input v-model="workflowDraft.project" placeholder="如 dw / risk / growth"
                       class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] text-slate-700">
              </label>
              <label class="block">
                <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">状态</span>
                <select v-model="workflowDraft.status" class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] text-slate-700">
                  <option value="draft">草稿</option>
                  <option value="active">已上线</option>
                  <option value="paused">暂停</option>
                  <option value="archived">归档</option>
                </select>
              </label>
            </div>
            <div class="grid grid-cols-2 gap-2">
              <label class="block">
                <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">负责人</span>
                <input v-model="workflowDraft.owner" placeholder="如 alice@team"
                       class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] text-slate-700">
              </label>
              <label class="block">
                <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">cron（可选）</span>
                <input v-model="workflowDraft.schedule_cron" placeholder="0 2 * * * 或留空"
                       class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-700">
              </label>
            </div>
            <label class="block">
              <span class="mb-0.5 block text-[10px] font-semibold text-slate-500">标签（逗号分隔）</span>
              <input :value="(workflowDraft.tags || []).join(', ')"
                     @input="workflowDraft.tags = $event.target.value.split(',').map(s => s.trim()).filter(Boolean)"
                     placeholder="orders, daily, prod"
                     class="block w-full rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-700">
            </label>

            <!-- 输入资产编辑器 -->
            <div>
              <div class="mb-1 flex items-center justify-between">
                <span class="text-[10px] font-semibold text-slate-500">输入资产</span>
                <button class="text-[10.5px] font-semibold text-blue-600 hover:underline"
                        @click="workflowDraft.input_assets.push({ key: '', kind: 'table', description: '' })">+ 添加</button>
              </div>
              <ul class="space-y-1">
                <li v-for="(asset, i) in workflowDraft.input_assets" :key="i" class="grid grid-cols-[minmax(0,1fr)_80px_24px] gap-1">
                  <input v-model="asset.key" placeholder="schema.table 或 路径"
                         class="rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-700">
                  <select v-model="asset.kind" class="rounded-md border border-slate-200 bg-white px-1 py-1 text-[11px] text-slate-700">
                    <option value="table">表</option>
                    <option value="file">文件</option>
                    <option value="stream">流</option>
                  </select>
                  <button class="rounded text-rose-600 hover:bg-rose-50" title="删除"
                          @click="workflowDraft.input_assets.splice(i, 1)">×</button>
                </li>
              </ul>
            </div>

            <!-- 输出资产编辑器 -->
            <div>
              <div class="mb-1 flex items-center justify-between">
                <span class="text-[10px] font-semibold text-slate-500">输出资产</span>
                <button class="text-[10.5px] font-semibold text-blue-600 hover:underline"
                        @click="workflowDraft.output_assets.push({ key: '', kind: 'table', description: '' })">+ 添加</button>
              </div>
              <ul class="space-y-1">
                <li v-for="(asset, i) in workflowDraft.output_assets" :key="i" class="grid grid-cols-[minmax(0,1fr)_80px_24px] gap-1">
                  <input v-model="asset.key" placeholder="schema.table 或 路径"
                         class="rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-[11px] text-slate-700">
                  <select v-model="asset.kind" class="rounded-md border border-slate-200 bg-white px-1 py-1 text-[11px] text-slate-700">
                    <option value="table">表</option>
                    <option value="file">文件</option>
                    <option value="stream">流</option>
                  </select>
                  <button class="rounded text-rose-600 hover:bg-rose-50" title="删除"
                          @click="workflowDraft.output_assets.splice(i, 1)">×</button>
                </li>
              </ul>
            </div>

            <p class="text-[10px] text-slate-400">这些字段保存后落 config/workflows.json，列表页和详情页都会读到。</p>
          </div>
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">输入资产 ({{ workflowDraft.input_assets.length }})</p>
          <ul v-if="workflowDraft.input_assets.length" class="space-y-1.5">
            <li v-for="(asset, i) in workflowDraft.input_assets" :key="i" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11.5px]"
                :title="asset.description">
              <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400"></span>
              <span class="truncate font-mono text-slate-700">{{ asset.key }}</span>
              <span class="ml-auto rounded bg-white px-1.5 py-0.5 font-mono text-[9.5px] text-slate-500">{{ asset.kind }}</span>
            </li>
          </ul>
          <p v-else class="text-[11px] text-slate-400">还没有声明输入资产 — 在「基础设置」面板下方添加</p>
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">输出资产 ({{ workflowDraft.output_assets.length }})</p>
          <ul v-if="workflowDraft.output_assets.length" class="space-y-1.5">
            <li v-for="(asset, i) in workflowDraft.output_assets" :key="i" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11.5px]"
                :title="asset.description">
              <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"></span>
              <span class="truncate font-mono text-slate-700">{{ asset.key }}</span>
              <span class="ml-auto rounded bg-white px-1.5 py-0.5 font-mono text-[9.5px] text-slate-500">{{ asset.kind }}</span>
            </li>
          </ul>
          <p v-else class="text-[11px] text-slate-400">还没有声明输出资产 — 在「基础设置」面板下方添加</p>
        </div>
      </aside>
    </div>

    <!-- 标签页区域 -->
    <div class="rounded-xl border border-slate-200 bg-white shadow-sm">
      <nav class="flex border-b border-slate-200 px-2 text-[12px]">
        <button v-for="t in tabs" :key="t.id"
                class="border-b-2 px-3 py-2 font-semibold transition"
                :class="activeTab === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'"
                @click="activeTab = t.id">
          {{ t.label }}
          <span v-if="t.id === 'history'" class="ml-1 rounded bg-slate-100 px-1 text-[10px] font-mono text-slate-500">{{ workflowRunHistory.length }}</span>
        </button>
      </nav>

      <!-- 运行历史 -->
      <div v-if="activeTab === 'history'" class="overflow-x-auto">
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
                            @click="runWorkflowAsyncWith(currentWorkflow.id)">
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

      <!-- 事件日志（基于最近一次运行合成） -->
      <div v-else-if="activeTab === 'events'" class="px-3 py-2">
        <div v-if="!recentEvents.length" class="py-8 text-center text-[12.5px] text-slate-400">
          还没有运行记录 — 跑过一次后，事件日志才会有内容
        </div>
        <div v-else class="font-mono text-[12px]">
          <div v-for="(ev, idx) in recentEvents" :key="idx" class="grid grid-cols-[140px_140px_1fr] items-start gap-3 border-b border-slate-100 px-2 py-1.5 last:border-0">
            <span class="text-[10.5px] text-slate-400">{{ ev.ts }}</span>
            <span class="flex items-center gap-1.5">
              <span class="grid h-4 w-4 shrink-0 place-items-center text-[11px]" :class="eventTypeMeta[ev.type]?.text || 'text-slate-500'">{{ eventTypeMeta[ev.type]?.glyph || '·' }}</span>
              <span class="text-[10.5px] font-bold uppercase tracking-wider" :class="eventTypeMeta[ev.type]?.text || 'text-slate-500'">{{ eventTypeMeta[ev.type]?.label || ev.type }}</span>
            </span>
            <div class="min-w-0">
              <p class="break-words" :class="levelClass(ev.level)">{{ ev.msg }}</p>
              <p v-if="ev.step" class="mt-0.5 text-[10.5px] text-slate-400">step={{ ev.step }}</p>
              <div v-if="ev.metadata" class="mt-1 flex flex-wrap gap-1">
                <span v-for="(v, k) in ev.metadata" :key="k" class="rounded bg-slate-100 px-1.5 py-0.5 text-[10.5px]"><span class="text-slate-500">{{ k }}=</span><span class="text-slate-700">{{ v }}</span></span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 依赖关系（输入 / 输出资产） -->
      <div v-else-if="activeTab === 'lineage'" class="grid grid-cols-2 gap-4 p-4">
        <div>
          <h3 class="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">输入资产 ({{ workflowDraft.input_assets.length }})</h3>
          <ul v-if="workflowDraft.input_assets.length" class="space-y-2">
            <li v-for="(asset, i) in workflowDraft.input_assets" :key="i" class="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
              <span class="h-2 w-2 rounded-full bg-slate-400"></span>
              <div class="min-w-0 flex-1">
                <p class="truncate font-mono text-[12.5px] font-semibold text-slate-800">{{ asset.key }}</p>
                <p v-if="asset.description" class="text-[11px] text-slate-500">{{ asset.description }}</p>
              </div>
              <span class="rounded bg-white px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ asset.kind }}</span>
            </li>
          </ul>
          <p v-else class="text-[12px] text-slate-400">还没有声明输入资产 — 在「基础设置」面板下方添加</p>
        </div>
        <div>
          <h3 class="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">输出资产 ({{ workflowDraft.output_assets.length }})</h3>
          <ul v-if="workflowDraft.output_assets.length" class="space-y-2">
            <li v-for="(asset, i) in workflowDraft.output_assets" :key="i" class="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
              <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
              <div class="min-w-0 flex-1">
                <p class="truncate font-mono text-[12.5px] font-semibold text-slate-800">{{ asset.key }}</p>
                <p v-if="asset.description" class="text-[11px] text-slate-500">{{ asset.description }}</p>
              </div>
              <span class="rounded bg-white px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ asset.kind }}</span>
            </li>
          </ul>
          <p v-else class="text-[12px] text-slate-400">还没有声明输出资产 — 在「基础设置」面板下方添加</p>
        </div>
      </div>

      <!-- 节点配置（保留原有编辑器） -->
      <div v-else-if="activeTab === 'config'" class="space-y-4 p-4">
        <div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <label>
            <span class="mb-2 block text-[10px] font-bold uppercase tracking-wider text-slate-400">作业流名称</span>
            <input v-model="workflowDraft.name" class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-blue-400 focus:outline-none">
          </label>
          <label>
            <span class="mb-2 block text-[10px] font-bold uppercase tracking-wider text-slate-400">默认变量（每行 key=value）</span>
            <textarea v-model="workflowDraft.default_variables" class="min-h-[60px] w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-[12.5px] focus:border-blue-400 focus:outline-none" placeholder="biz_date=2026-05-01"></textarea>
          </label>
        </div>

        <div class="rounded-xl border border-slate-200 bg-slate-50/40 p-3">
          <div class="mb-3 flex items-center justify-between">
            <h3 class="text-sm font-bold text-slate-700">节点 ({{ workflowDraft.nodes.length }})</h3>
            <button class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-blue-700" @click="addWorkflowNode">+ 新增节点</button>
          </div>
          <div v-if="!workflowDraft.nodes.length" class="rounded-lg border border-dashed border-slate-200 bg-white p-6 text-center text-sm text-slate-400">还没有节点 — 点右上角「+ 新增节点」开始（推荐 params 起步，再接 compare/lineage/http）</div>
          <div v-else class="space-y-2">
            <div v-for="(node, index) in workflowDraft.nodes" :key="index" class="rounded-lg border border-slate-200 bg-white p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <div class="flex items-center gap-2">
                  <span class="grid h-6 w-6 place-items-center rounded bg-blue-600 text-[10px] font-bold text-white">{{ index + 1 }}</span>
                  <input v-model="node.id" class="w-24 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-mono">
                  <span class="rounded px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase"
                        :class="{
                          'bg-sky-50 text-sky-700': node.type === 'params',
                          'bg-blue-50 text-blue-700': node.type === 'compare',
                          'bg-emerald-50 text-emerald-700': node.type === 'lineage',
                          'bg-amber-50 text-amber-700': node.type === 'excel_export',
                          'bg-purple-50 text-purple-700': node.type === 'http',
                        }">{{ node.type }}</span>
                </div>
                <div class="flex items-center gap-1">
                  <button class="rounded-md border border-slate-200 px-2 py-1 text-[10px] font-bold text-slate-600 transition hover:bg-slate-50 disabled:opacity-30" :disabled="index === 0" @click="moveWorkflowNode(index, -1)">↑</button>
                  <button class="rounded-md border border-slate-200 px-2 py-1 text-[10px] font-bold text-slate-600 transition hover:bg-slate-50 disabled:opacity-30" :disabled="index === workflowDraft.nodes.length - 1" @click="moveWorkflowNode(index, 1)">↓</button>
                  <button class="rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-[10px] font-bold text-rose-700 transition hover:bg-rose-100" @click="removeWorkflowNode(index)">删除</button>
                </div>
              </div>
              <div class="grid grid-cols-1 gap-2 lg:grid-cols-3">
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">类型</span>
                  <select v-model="node.type" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                    <option value="params">参数准备 params</option>
                    <option value="compare">数据对比 compare</option>
                    <option value="lineage">血缘分析 lineage</option>
                    <option value="excel_export">Excel 导出 excel_export</option>
                    <option value="http">HTTP 请求 http</option>
                  </select>
                </label>
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">显示名称</span>
                  <input v-model="node.name" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                </label>
              </div>
              <!-- compare 节点编辑器（含任务选择 + SQL drill-in 覆盖） -->
              <WorkflowCompareNodeEditor v-if="node.type === 'compare'" :node="node" class="mt-3" />

              <!-- params 节点编辑器（参数列表 + 引用语法速查） -->
              <WorkflowParamsNodeEditor v-if="node.type === 'params'" :node="node" class="mt-3" />

              <!-- lineage 节点编辑器（SQL + 方言） -->
              <WorkflowLineageNodeEditor v-if="node.type === 'lineage'" :node="node" class="mt-2" />
              <div v-if="node.type === 'http'" class="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-[100px_minmax(0,1fr)_120px]">
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">方法</span>
                  <select v-model="node.method" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                    <option>GET</option><option>POST</option><option>PUT</option>
                  </select>
                </label>
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">URL</span>
                  <input v-model="node.url" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs">
                </label>
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">期望状态</span>
                  <input v-model="node.expect_status" type="number" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                </label>
              </div>

              <!-- excel_export 节点编辑器（sheet 列表 + 节点输出 / 历史 run 切换） -->
              <WorkflowExcelExportNodeEditor v-if="node.type === 'excel_export'" :node="node" class="mt-3" />
              <div v-if="otherNodeIds(node.id).length" class="mt-2">
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">依赖（depends_on）</span>
                <div class="flex flex-wrap gap-1.5">
                  <label v-for="otherId in otherNodeIds(node.id)" :key="otherId" class="flex cursor-pointer items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-0.5 text-xs hover:bg-blue-50">
                    <input type="checkbox" :value="otherId" v-model="node.depends_on" class="h-3 w-3 rounded">
                    <span class="font-mono text-slate-700">{{ otherId }}</span>
                  </label>
                </div>
              </div>
              <label class="mt-2 block">
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">条件 when（可选）</span>
                <input v-model="node.when" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs" placeholder="${nodes.x.summary.diff} > 0">
              </label>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>
