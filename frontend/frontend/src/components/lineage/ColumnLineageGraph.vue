<script setup>
import { computed, ref, watch } from 'vue'
import { GitBranch, Search } from 'lucide-vue-next'

const props = defineProps({
  edges: { type: Array, default: () => [] },
  impact: { type: Object, default: () => ({ downstream: {} }) },
  focusNodeId: { type: String, default: '' },
})

const search = ref('')
const confidenceFilter = ref('all')
const onlyLowConfidence = ref(false)
const onlyAmbiguous = ref(false)
const hopDepth = ref('all')
const selectedId = ref('')

const normalizeNodeId = (table, column) => {
  let col = String(column || '').trim()
  let tbl = String(table || '').trim()
  if (!col) return ''
  if (!tbl && col.includes('.')) {
    const parts = col.split('.')
    col = parts.pop()
    tbl = parts.join('.')
  }
  return `${tbl || 'unknown'}.${col || 'unknown'}`.toLowerCase()
}

const splitNode = (id) => {
  const at = String(id || '').lastIndexOf('.')
  if (at < 0) return { table: 'unknown', column: id || 'unknown' }
  return { table: id.slice(0, at), column: id.slice(at + 1) }
}

const baseEdges = computed(() => {
  const out = []
  for (const edge of props.edges || []) {
    const source = normalizeNodeId(edge.source_table, edge.source_column)
    const target = normalizeNodeId(edge.target_table, edge.target_column)
    if (!source || !target) continue
    out.push({
      id: `${source}->${target}:${edge.statement_index || ''}:${edge.file_name || ''}`,
      source,
      target,
      confidence: edge.confidence || 'high',
      transform: edge.transform || '',
      statement_index: edge.statement_index || '',
      file_name: edge.file_name || '',
      warnings: edge.warnings || [],
    })
  }
  return out
})

const confidences = computed(() => [...new Set(baseEdges.value.map((edge) => edge.confidence))].sort())

const adjacent = computed(() => {
  const out = new Map()
  const reverse = new Map()
  for (const edge of baseEdges.value) {
    if (!out.has(edge.source)) out.set(edge.source, [])
    if (!reverse.has(edge.target)) reverse.set(edge.target, [])
    out.get(edge.source).push(edge.target)
    reverse.get(edge.target).push(edge.source)
  }
  return { out, reverse }
})

const hopSet = computed(() => {
  if (!selectedId.value || hopDepth.value === 'all') return null
  const depthLimit = Number(hopDepth.value) || 1
  const seen = new Set([selectedId.value])
  let frontier = [selectedId.value]
  for (let depth = 0; depth < depthLimit; depth += 1) {
    const next = []
    for (const node of frontier) {
      for (const n of adjacent.value.out.get(node) || []) {
        if (!seen.has(n)) { seen.add(n); next.push(n) }
      }
      for (const n of adjacent.value.reverse.get(node) || []) {
        if (!seen.has(n)) { seen.add(n); next.push(n) }
      }
    }
    frontier = next
    if (!frontier.length) break
  }
  return seen
})

const visibleEdges = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return baseEdges.value.filter((edge) => {
    if (confidenceFilter.value !== 'all' && edge.confidence !== confidenceFilter.value) return false
    if (onlyLowConfidence.value && edge.confidence === 'high') return false
    if (onlyAmbiguous.value && !edge.source.startsWith('unknown.')) return false
    if (kw && !`${edge.source} ${edge.target} ${edge.transform} ${edge.file_name}`.toLowerCase().includes(kw)) return false
    if (hopSet.value && (!hopSet.value.has(edge.source) || !hopSet.value.has(edge.target))) return false
    return true
  })
})

const graph = computed(() => {
  const nodes = new Map()
  const addNode = (id, role) => {
    if (!nodes.has(id)) {
      nodes.set(id, { id, ...splitNode(id), role, edge_count: 0 })
    } else {
      const node = nodes.get(id)
      node.role = node.role === role ? role : 'both'
    }
    nodes.get(id).edge_count += 1
  }
  for (const edge of visibleEdges.value) {
    addNode(edge.source, 'source')
    addNode(edge.target, 'target')
  }

  const layer = new Map([...nodes.keys()].map((id) => [id, 0]))
  for (let i = 0; i < nodes.size; i += 1) {
    let changed = false
    for (const edge of visibleEdges.value) {
      const next = Math.max(layer.get(edge.target) || 0, (layer.get(edge.source) || 0) + 1)
      if (next !== layer.get(edge.target)) {
        layer.set(edge.target, next)
        changed = true
      }
    }
    if (!changed) break
  }

  const grouped = new Map()
  for (const node of nodes.values()) {
    const key = layer.get(node.id) || 0
    if (!grouped.has(key)) grouped.set(key, [])
    grouped.get(key).push(node)
  }

  const positioned = []
  for (const [layerIndex, list] of [...grouped.entries()].sort((a, b) => a[0] - b[0])) {
    list.sort((a, b) => a.id.localeCompare(b.id))
    list.forEach((node, index) => {
      positioned.push({ ...node, x: 80 + layerIndex * 260, y: 70 + index * 86 })
    })
  }
  const pos = new Map(positioned.map((node) => [node.id, node]))
  const width = Math.max(760, Math.max(0, ...positioned.map((node) => node.x)) + 220)
  const height = Math.max(320, Math.max(0, ...positioned.map((node) => node.y)) + 90)
  return { nodes: positioned, pos, width, height }
})

const selectedNode = computed(() => graph.value.pos.get(selectedId.value) || null)
const selectedEdges = computed(() => baseEdges.value.filter((edge) => edge.source === selectedId.value || edge.target === selectedId.value))

const nodeClass = (node) => {
  if (node.id === selectedId.value) return 'fill-violet-600 stroke-violet-700'
  if (node.role === 'source') return 'fill-blue-50 stroke-blue-200'
  if (node.role === 'target') return 'fill-emerald-50 stroke-emerald-200'
  return 'fill-amber-50 stroke-amber-200'
}
const nodeTextClass = (node) => node.id === selectedId.value ? 'fill-white' : 'fill-slate-700'
const edgeClass = (edge) => {
  if (edge.confidence === 'low') return 'stroke-rose-400'
  if (edge.confidence === 'medium') return 'stroke-amber-400'
  return 'stroke-slate-300'
}

watch(
  () => props.focusNodeId,
  (value) => {
    if (value) {
      selectedId.value = value.toLowerCase()
      hopDepth.value = '2'
    }
  },
  { immediate: true },
)
</script>

<template>
  <section class="space-y-3">
    <div class="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
      <div class="relative min-w-[240px] flex-1">
        <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
        <input v-model="search" class="h-8 w-full rounded-lg border border-slate-200 bg-slate-50 pl-8 pr-2 text-sm outline-none focus:border-violet-300" placeholder="搜索 table.column / transform / file">
      </div>
      <select v-model="confidenceFilter" class="filter-select">
        <option value="all">全部可信度</option>
        <option v-for="item in confidences" :key="item" :value="item">{{ item }}</option>
      </select>
      <select v-model="hopDepth" class="filter-select" title="选中节点后，只看上下游 N 层">
        <option value="all">全部层级</option>
        <option value="1">上下游 1 层</option>
        <option value="2">上下游 2 层</option>
        <option value="3">上下游 3 层</option>
      </select>
      <label class="inline-flex items-center gap-1 text-[11px] text-slate-600">
        <input v-model="onlyLowConfidence" type="checkbox" class="h-3.5 w-3.5">
        仅低可信度
      </label>
      <label class="inline-flex items-center gap-1 text-[11px] text-slate-600">
        <input v-model="onlyAmbiguous" type="checkbox" class="h-3.5 w-3.5">
        仅未知来源
      </label>
    </div>

    <div v-if="!baseEdges.length" class="rounded-xl border border-dashed border-slate-200 bg-white py-10 text-center text-slate-400">
      <GitBranch class="mx-auto mb-2 h-8 w-8 text-slate-300" />
      <p class="text-sm">暂无字段级图数据</p>
    </div>

    <div v-else class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div class="overflow-auto rounded-xl border border-slate-200 bg-white">
        <svg :width="graph.width" :height="graph.height" class="min-w-full">
          <defs>
            <marker id="column-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L9,3 z" fill="#94a3b8" />
            </marker>
          </defs>
          <g>
            <path
              v-for="edge in visibleEdges"
              :key="edge.id"
              :d="`M ${graph.pos.get(edge.source)?.x + 160 || 0} ${graph.pos.get(edge.source)?.y || 0} C ${graph.pos.get(edge.source)?.x + 220 || 0} ${graph.pos.get(edge.source)?.y || 0}, ${graph.pos.get(edge.target)?.x - 60 || 0} ${graph.pos.get(edge.target)?.y || 0}, ${graph.pos.get(edge.target)?.x || 0} ${graph.pos.get(edge.target)?.y || 0}`"
              fill="none"
              stroke-width="1.6"
              marker-end="url(#column-arrow)"
              :class="edgeClass(edge)"
            />
          </g>
          <g
            v-for="node in graph.nodes"
            :key="node.id"
            class="cursor-pointer"
            @click="selectedId = node.id"
          >
            <rect :x="node.x" :y="node.y - 24" width="170" height="48" rx="10" class="stroke" :class="nodeClass(node)" />
            <text :x="node.x + 12" :y="node.y - 4" class="text-[11px] font-semibold" :class="nodeTextClass(node)">{{ node.column }}</text>
            <text :x="node.x + 12" :y="node.y + 13" class="text-[10px]" :class="nodeTextClass(node)">{{ node.table }}</text>
          </g>
        </svg>
      </div>

      <aside class="rounded-xl border border-slate-200 bg-white p-4">
        <div v-if="selectedNode">
          <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">选中字段</p>
          <h3 class="mt-1 break-all font-mono text-sm font-bold text-slate-800">{{ selectedNode.id }}</h3>
          <p class="mt-1 text-xs text-slate-500">{{ selectedNode.role }} · {{ selectedNode.edge_count }} 条边</p>
          <div class="mt-3 space-y-2">
            <div v-for="edge in selectedEdges" :key="edge.id" class="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
              <p class="break-all font-mono text-[11px] text-slate-700">{{ edge.source }} → {{ edge.target }}</p>
              <p class="mt-1 text-[11px] text-slate-500">{{ edge.confidence }} · 语句 {{ edge.statement_index || '-' }}</p>
              <p v-if="edge.transform" class="mt-1 break-all font-mono text-[11px] text-slate-500">{{ edge.transform }}</p>
            </div>
          </div>
        </div>
        <div v-else class="py-8 text-center text-sm text-slate-400">
          点击字段节点查看上下游明细
        </div>
      </aside>
    </div>
  </section>
</template>
