<script setup>
import { computed, defineAsyncComponent, inject, ref, watch } from 'vue'
import { healthMeta, nodeStatusMeta, getMeta, layoutDAG, workflowHealth, synthesizeEvents, parameterTypeMeta, resolveAllParameters } from '../../mock/workflow_meta'

const SqlEditor = defineAsyncComponent(() => import('../../components/SqlEditor.vue'))

const emit = defineEmits(['back', 'open-run'])

const {
  state, workflowDraft, selectedWorkflowId, currentWorkflow, isSavedWorkflow,
  workflowResult, workflowAsyncJob, workflowAsyncStatus, workflowRunHistory,
  saveWorkflow, deleteWorkflow,
  runWorkflow, runWorkflowAsync, cancelWorkflowAsync,
  addWorkflowNode, removeWorkflowNode, moveWorkflowNode,
  loadWorkflowRunDetail,
} = inject('app')

const activeTab = ref('history')   // history / events / lineage / config / quality
const selectedNodeId = ref('')

// 新建态：自动落到「节点配置」tab，引导用户开始编辑
watch(selectedWorkflowId, (id) => {
  if (id === 'new') activeTab.value = 'config'
}, { immediate: true })

// 元数据补全（从 mock 拿）。索引用工作流在数组中的位置。
const workflowIndex = computed(() => state.workflows.findIndex((wf) => wf.id === selectedWorkflowId.value))
const meta = computed(() => getMeta(currentWorkflow.value, workflowIndex.value === -1 ? 0 : workflowIndex.value))

// 参数解析：把每个参数定义解析成下次运行将使用的具体值（预览用）。
const resolvedParams = computed(() => resolveAllParameters(meta.value.parameters || []))

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

const otherNodeIds = (currentId) => workflowDraft.nodes.map((n) => n.id).filter((id) => id && id !== currentId)

const tabs = [
  { id: 'history',  label: '运行历史' },
  { id: 'events',   label: '事件日志' },
  { id: 'lineage',  label: '依赖关系' },
  { id: 'config',   label: '节点配置' },
  { id: 'quality',  label: '质量检查' },
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
            <span class="text-slate-300">/</span>
            <span class="font-mono">{{ meta.project }}</span>
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
              <span class="font-mono text-[11.5px] text-slate-500">{{ meta.schedule.text }}</span>
              <span class="text-slate-300">·</span>
              <span class="text-[11.5px] text-slate-500">负责人 <span class="font-medium text-slate-700">{{ meta.owner }}</span></span>
            </template>
          </div>
          <div v-if="selectedWorkflowId !== 'new'" class="mt-1.5 flex flex-wrap gap-1">
            <span v-for="tag in meta.tags" :key="tag" class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ tag }}</span>
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
                    :class="selectedNodeId === n.id ? 'border-blue-400 ring-2 ring-blue-200' : 'border-slate-200'"
                    :style="{ left: n.x + 'px', top: n.y + 'px', width: '220px', height: '84px' }"
                    @click="selectedNodeId = n.id">
              <div class="flex items-center gap-1.5">
                <span class="h-2 w-2 shrink-0 rounded-full" :class="nodeStatusMeta[nodeStatus(n.id)].dot"></span>
                <span class="truncate text-[12.5px] font-semibold text-slate-800">{{ n.name || n.id }}</span>
              </div>
              <div class="flex items-center gap-1.5 text-[10.5px]">
                <span class="rounded px-1 font-mono text-[9.5px] font-bold uppercase"
                      :class="{
                        'bg-blue-50 text-blue-700': n.type === 'compare',
                        'bg-emerald-50 text-emerald-700': n.type === 'lineage',
                        'bg-purple-50 text-purple-700': n.type === 'http',
                      }">{{ n.type }}</span>
                <span class="font-mono text-slate-500">{{ n.id }}</span>
                <span v-if="nodeStatusByid[n.id]" class="ml-auto font-mono text-slate-500">{{ nodeStatusByid[n.id].elapsed_seconds }}s</span>
              </div>
              <span v-if="nodeStatusByid[n.id]?.error" class="line-clamp-1 text-[10px] text-rose-600">{{ nodeStatusByid[n.id].error }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 元数据侧栏 -->
      <aside class="flex flex-col gap-3">
        <!-- 运行参数：参数驱动作业流的核心信息 -->
        <div class="rounded-xl border border-slate-200 bg-white shadow-sm">
          <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
            <p class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">运行参数</p>
            <span class="text-[10.5px] text-slate-500">{{ (meta.parameters || []).length }} 个</span>
          </div>
          <ul class="divide-y divide-slate-100">
            <li v-for="param in (meta.parameters || [])" :key="param.name" class="px-3 py-2.5">
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
            <li v-if="!(meta.parameters || []).length" class="px-3 py-3 text-center text-[11px] text-slate-400">暂无参数定义</li>
          </ul>
          <div class="border-t border-slate-100 px-3 py-2 text-[10.5px] text-slate-500">
            可在 SQL / 文件名 / Sheet 名等位置用 <code class="rounded bg-slate-100 px-1 font-mono">${'$'}{name}</code> 引用
          </div>
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">概要</p>
          <p class="text-[12.5px] leading-relaxed text-slate-700">{{ meta.description }}</p>
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">输入资产 ({{ meta.input_assets.length }})</p>
          <ul class="space-y-1.5">
            <li v-for="asset in meta.input_assets" :key="asset.key" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11.5px]">
              <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="healthMeta[asset.health]?.dot || 'bg-slate-300'"></span>
              <span class="truncate font-mono text-slate-700">{{ asset.key }}</span>
              <span class="ml-auto rounded bg-white px-1.5 py-0.5 font-mono text-[9.5px] text-slate-500">{{ asset.kind }}</span>
            </li>
          </ul>
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">输出资产 ({{ meta.output_assets.length }})</p>
          <ul class="space-y-1.5">
            <li v-for="asset in meta.output_assets" :key="asset.key" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11.5px]">
              <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"></span>
              <span class="truncate font-mono text-slate-700">{{ asset.key }}</span>
              <span class="ml-auto text-[10.5px] text-slate-500">下游 {{ asset.downstream_count }}</span>
            </li>
          </ul>
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">告警 / 资源</p>
          <dl class="space-y-1.5 text-[11.5px]">
            <div class="flex justify-between gap-2"><dt class="text-slate-500">资源队列</dt><dd class="font-mono text-slate-700">{{ meta.queue }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">告警目标</dt><dd class="text-right text-slate-700">{{ meta.alert_targets.join(' · ') }}</dd></div>
          </dl>
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
          <span v-if="t.id === 'quality'" class="ml-1 rounded bg-slate-100 px-1 text-[10px] font-mono text-slate-500">{{ meta.quality_checks.length }}</span>
        </button>
      </nav>

      <!-- 运行历史 -->
      <div v-if="activeTab === 'history'" class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="border-b border-slate-200 bg-slate-50/60">
            <tr class="text-left">
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
            <tr v-for="run in workflowRunHistory" :key="run.run_id" class="border-b border-slate-100 last:border-0 hover:bg-slate-50/70">
              <td class="px-3 py-2.5">
                <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset"
                      :class="run.status === 'success' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : 'bg-rose-50 text-rose-700 ring-rose-200'">
                  <span class="h-1.5 w-1.5 rounded-full" :class="run.status === 'success' ? 'bg-emerald-500' : 'bg-rose-500'"></span>
                  {{ run.status === 'success' ? '成功' : '失败' }}
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
              <td class="px-3 py-2.5 text-right">
                <button class="rounded bg-slate-700 px-2 py-1 text-[10px] font-bold text-white transition hover:bg-blue-600" @click="emit('open-run', run.run_id)">查看 →</button>
              </td>
            </tr>
            <tr v-if="!workflowRunHistory.length"><td colspan="7" class="py-8 text-center text-[12.5px] text-slate-400">还没有历史运行</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 事件日志（基于最近一次运行合成） -->
      <div v-else-if="activeTab === 'events'" class="px-3 py-2">
        <div v-if="!recentEvents.length" class="py-8 text-center text-[12.5px] text-slate-400">
          还没有运行记录
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
          <h3 class="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">输入资产</h3>
          <ul class="space-y-2">
            <li v-for="asset in meta.input_assets" :key="asset.key" class="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
              <span class="h-2 w-2 rounded-full" :class="healthMeta[asset.health]?.dot || 'bg-slate-300'"></span>
              <div class="min-w-0 flex-1">
                <p class="truncate font-mono text-[12.5px] font-semibold text-slate-800">{{ asset.key }}</p>
                <p class="text-[11px] text-slate-500">类型 {{ asset.kind }} · 最近物化 {{ asset.last_materialized }}</p>
              </div>
              <span class="rounded bg-white px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ asset.kind }}</span>
            </li>
          </ul>
        </div>
        <div>
          <h3 class="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">输出资产 / 下游影响</h3>
          <ul class="space-y-2">
            <li v-for="asset in meta.output_assets" :key="asset.key" class="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
              <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
              <div class="min-w-0 flex-1">
                <p class="truncate font-mono text-[12.5px] font-semibold text-slate-800">{{ asset.key }}</p>
                <p class="text-[11px] text-slate-500">影响 {{ asset.downstream_count }} 个下游资产</p>
              </div>
              <span class="rounded bg-white px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ asset.kind }}</span>
            </li>
          </ul>
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
          <div v-if="!workflowDraft.nodes.length" class="rounded-lg border border-dashed border-slate-200 bg-white p-6 text-center text-sm text-slate-400">还没有节点</div>
          <div v-else class="space-y-2">
            <div v-for="(node, index) in workflowDraft.nodes" :key="index" class="rounded-lg border border-slate-200 bg-white p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <div class="flex items-center gap-2">
                  <span class="grid h-6 w-6 place-items-center rounded bg-blue-600 text-[10px] font-bold text-white">{{ index + 1 }}</span>
                  <input v-model="node.id" class="w-24 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-mono">
                  <span class="rounded px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase"
                        :class="{ 'bg-blue-50 text-blue-700': node.type === 'compare', 'bg-emerald-50 text-emerald-700': node.type === 'lineage', 'bg-purple-50 text-purple-700': node.type === 'http' }">{{ node.type }}</span>
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
                    <option value="compare">数据对比 compare</option>
                    <option value="lineage">血缘分析 lineage</option>
                    <option value="http">HTTP 请求 http</option>
                  </select>
                </label>
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">显示名称</span>
                  <input v-model="node.name" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                </label>
                <label v-if="node.type === 'compare'">
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">引用对比任务</span>
                  <select v-model="node.task_id" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                    <option value="">— 选择 —</option>
                    <option v-for="task in compareTaskOptions" :key="task.id" :value="task.id">{{ task.name }}</option>
                  </select>
                </label>
              </div>
              <div v-if="node.type === 'lineage'" class="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr)_140px]">
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">SQL</span>
                  <textarea v-model="node.sql" class="block min-h-[60px] w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 font-mono text-[12px]" placeholder="SELECT * FROM ..."></textarea>
                </label>
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">方言</span>
                  <input v-model="node.dialect" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs" placeholder="mysql">
                </label>
              </div>
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

      <!-- 质量检查 -->
      <div v-else-if="activeTab === 'quality'" class="space-y-2 p-4">
        <div v-for="check in meta.quality_checks" :key="check.name" class="rounded-xl border border-slate-200 bg-white p-3">
          <div class="flex items-center gap-2.5">
            <span class="h-2 w-2 rounded-full" :class="check.status === 'passed' ? 'bg-emerald-500' : 'bg-rose-500'"></span>
            <span class="font-mono text-[13.5px] font-semibold text-slate-800">{{ check.name }}</span>
            <span class="rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="check.severity === '阻断' ? 'text-rose-700 bg-rose-50 ring-rose-200' : 'text-amber-700 bg-amber-50 ring-amber-200'">{{ check.severity }}</span>
            <span class="ml-auto rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset" :class="check.status === 'passed' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : 'bg-rose-50 text-rose-700 ring-rose-200'">{{ check.status === 'passed' ? '通过' : '未通过' }}</span>
          </div>
          <p class="mt-1.5 text-[12px] text-slate-600">{{ check.message }}</p>
          <p class="mt-1 font-mono text-[10.5px] text-slate-400">最近评估：{{ check.last_run }}</p>
        </div>
      </div>
    </div>
  </div>
</template>
