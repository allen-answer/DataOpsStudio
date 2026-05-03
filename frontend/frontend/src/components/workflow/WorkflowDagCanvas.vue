<script setup>
import { computed, ref } from 'vue'
import { nodeStatusMeta, layoutDAG } from '../../mock/workflow_meta'

// DAG 画布：把 workflowDraft.nodes 自动布局成 svg 流向图，叠加上次运行的状态。
// 父组件控制选中节点（v-model:selectedNodeId）；空态点 "+ 添加" 走 emit('add-node')
// 让父决定跳 tab 或别的副作用。
const props = defineProps({
  nodes:        { type: Array,  required: true },   // workflowDraft.nodes
  latestRun:    { type: Object, default: null  },   // workflowResult（最近一次运行，叠节点状态用）
  selectedNodeId: { type: String, default: '' },
})
const emit = defineEmits(['update:selectedNodeId', 'add-node'])

const setSelected = (id) => emit('update:selectedNodeId', id)

const nodeStatusByid = computed(() => {
  const map = {}
  if (props.latestRun) {
    for (const n of props.latestRun.nodes || []) map[n.node_id] = n
  }
  return map
})

const layout = computed(() => {
  const nodes = props.nodes.map((n) => ({
    id: n.id,
    name: n.name,
    type: n.type,
    depends_on: n.depends_on || [],
    input_mode: n.input_mode || '',
    script_filename: n.script_filename || '',
    script_path: n.script_path || '',
  }))
  return layoutDAG(nodes, { nodeW: 220, nodeH: 84, gapX: 80, gapY: 28, padX: 40, padY: 40 })
})

const positionedById = computed(() => {
  const map = {}
  for (const n of layout.value.positioned) map[n.id] = n
  return map
})

const allEdges = computed(() => {
  const out = []
  for (const n of props.nodes) {
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
  props.selectedNodeId && (edge.source === props.selectedNodeId || edge.target === props.selectedNodeId)

const nodeStatus = (nodeId) => nodeStatusByid.value[nodeId]?.status || 'pending'
const lineageInputLabel = (node) => {
  if (node.type !== 'lineage') return ''
  const mode = node.input_mode || (node.script_path ? 'uploaded_file' : 'inline_sql')
  if (mode === 'uploaded_zip') return `ZIP ${node.script_filename || node.script_path || 'not selected'}`
  if (mode === 'uploaded_file') return `FILE ${node.script_filename || node.script_path || 'not selected'}`
  return 'SQL inline'
}

// DAG canvas 节点 box 描边色 —— 失败/跳过的节点要醒目，光看小圆点容易漏。
// selected 仍走蓝环；非 selected 时按状态着色。
const nodeBoxClass = (nodeId) => {
  if (props.selectedNodeId === nodeId) return 'border-blue-400 ring-2 ring-blue-200'
  const status = nodeStatus(nodeId)
  if (status === 'failed')   return 'border-rose-300 bg-rose-50/50'
  if (status === 'skipped')  return 'border-amber-300 bg-amber-50/40'
  if (status === 'running')  return 'border-blue-300 ring-1 ring-blue-100 animate-pulse'
  if (status === 'success')  return 'border-emerald-200'
  return 'border-slate-200'
}

// hover tooltip：完整 error / 时间 / node_id 等。pointer-events-none 避免遮挡节点。
const NODE_BOX_W = 220
const NODE_BOX_H = 84
const TOOLTIP_W = 300
const TOOLTIP_H = 160
const hoveredNodeId = ref('')
const hoveredNode = computed(() => layout.value.positioned.find((n) => n.id === hoveredNodeId.value) || null)
const hoveredStatus = computed(() => nodeStatusByid.value[hoveredNodeId.value] || null)
const hoveredHasInfo = computed(() => Boolean(hoveredStatus.value || hoveredNode.value))

// 默认放节点右侧；右边放不下则放左侧；都放不下就上方。
const tooltipStyle = computed(() => {
  if (!hoveredNode.value) return { display: 'none' }
  const n = hoveredNode.value
  const placedRight = n.x + NODE_BOX_W + 12 + TOOLTIP_W <= layout.value.width
  const left = placedRight ? n.x + NODE_BOX_W + 12 : Math.max(8, n.x - TOOLTIP_W - 12)
  const top = Math.min(n.y, Math.max(8, layout.value.height - TOOLTIP_H - 8))
  return { left: left + 'px', top: top + 'px', width: TOOLTIP_W + 'px' }
})
</script>

<template>
  <div class="flex flex-col rounded-xl border border-slate-200 bg-white shadow-sm">
    <div class="flex items-center justify-between border-b border-slate-200 bg-slate-50/60 px-3 py-1.5">
      <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">DAG · 节点 {{ nodes.length }} · 依赖 {{ allEdges.length }}</span>
      <div class="flex items-center gap-3 text-[10.5px] text-slate-500">
        <span v-for="(meta2, key) in nodeStatusMeta" :key="key" class="flex items-center gap-1">
          <span class="h-1.5 w-1.5 rounded-full" :class="meta2.dot"></span>{{ meta2.label }}
        </span>
      </div>
    </div>
    <div class="relative max-h-[520px] flex-1 overflow-auto">
      <div v-if="!nodes.length" class="grid h-[280px] place-items-center">
        <div class="text-center">
          <p class="text-sm text-slate-400">还没有节点</p>
          <button class="mt-2 inline-flex h-7 items-center gap-1 rounded-lg bg-blue-600 px-2.5 text-xs font-semibold text-white transition hover:bg-blue-700" @click="emit('add-node')">+ 在「节点配置」中添加</button>
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
                @click="setSelected(n.id)"
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
          <span v-if="lineageInputLabel(n)" class="line-clamp-1 font-mono text-[10px] text-emerald-700">{{ lineageInputLabel(n) }}</span>
          <span v-if="nodeStatusByid[n.id]?.error" class="line-clamp-1 text-[10px] text-rose-600">{{ nodeStatusByid[n.id].error }}</span>
        </button>

        <!-- hover tooltip -->
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
</template>
