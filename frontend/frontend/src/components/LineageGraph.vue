<script setup>
import { Graph } from '@antv/g6'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  groups: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
})

const graphEl = ref(null)
const searchText = ref('')
const matchIndex = ref(0)
const focusMode = ref('all')
const roleFilter = ref('all')
const edgeTypeFilter = ref('all')
const confidenceFilter = ref('all')
const scriptFilter = ref('')
const schemaFilter = ref('all')
const selectedItem = ref(null)
const collapsedSchemas = ref(new Set())
const collapsedScripts = ref(new Set())
const largeGraphExpanded = ref(false)
let graph = null

const edgeKey = (source, target) => `${source}|||${target}`
const schemaName = (name) => (name.includes('.') ? name.split('.').slice(0, -1).join('.') : '(默认)')
const unique = (items) => Array.from(new Set(items.filter(Boolean)))

const edgeDetails = computed(() => {
  const byKey = new Map()
  props.edges.forEach((edge) => {
    const key = edgeKey(edge.source_table, edge.target_table)
    if (!byKey.has(key)) byKey.set(key, [])
    byKey.get(key).push(edge)
  })
  return byKey
})

const allGraphData = computed(() => {
  const nodes = new Map()
  const edges = []
  props.groups.forEach((group) => {
    const target = group.target_table
    if (!target) return
    nodes.set(target, { id: target, data: { role: 'target', schema: schemaName(target) } })
    ;(group.source_tables || []).forEach((source) => {
      nodes.set(source, { id: source, data: { role: 'source', schema: schemaName(source) } })
      const details = edgeDetails.value.get(edgeKey(source, target)) || []
      edges.push({ id: edgeKey(source, target), source, target, data: { details, dependency: false } })
    })
    ;(group.dependency_tables || []).forEach((source) => {
      nodes.set(source, { id: source, data: { role: 'dependency', schema: schemaName(source) } })
      const details = edgeDetails.value.get(edgeKey(source, target)) || []
      edges.push({ id: edgeKey(source, target), source, target, data: { details, dependency: true } })
    })
  })
  return { nodes: Array.from(nodes.values()), edges }
})

const schemas = computed(() => unique(allGraphData.value.nodes.map((node) => node.data.schema)))
const scripts = computed(() => unique(props.edges.map((edge) => edge.file_name)))
const edgeTypes = computed(() => unique(props.edges.map((edge) => edge.edge_type)))
const confidences = computed(() => unique(props.edges.map((edge) => edge.confidence)))
const searchMatches = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  if (!keyword) return []
  return allGraphData.value.nodes.filter((node) => node.id.toLowerCase().includes(keyword))
})
const matchedNodeId = computed(() => searchMatches.value[matchIndex.value % Math.max(searchMatches.value.length, 1)]?.id || '')
const largeGraphLimited = computed(() => allGraphData.value.nodes.length > 300 && !largeGraphExpanded.value && !searchText.value.trim())

const filteredBase = computed(() => {
  let nodes = allGraphData.value.nodes
  let edges = allGraphData.value.edges
  if (roleFilter.value !== 'all') nodes = nodes.filter((node) => node.data.role === roleFilter.value)
  if (schemaFilter.value !== 'all') nodes = nodes.filter((node) => node.data.schema === schemaFilter.value)
  if (collapsedSchemas.value.size) nodes = nodes.filter((node) => !collapsedSchemas.value.has(node.data.schema))
  const nodeIds = new Set(nodes.map((node) => node.id))
  edges = edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
  if (edgeTypeFilter.value !== 'all') {
    edges = edges.filter((edge) => edge.data.details.some((detail) => detail.edge_type === edgeTypeFilter.value))
  }
  if (confidenceFilter.value !== 'all') {
    edges = edges.filter((edge) => edge.data.details.some((detail) => detail.confidence === confidenceFilter.value))
  }
  if (scriptFilter.value) {
    edges = edges.filter((edge) => edge.data.details.some((detail) => detail.file_name === scriptFilter.value))
  }
  if (collapsedScripts.value.size) {
    edges = edges.filter((edge) => !edge.data.details.some((detail) => collapsedScripts.value.has(detail.file_name)))
  }
  const connected = new Set(edges.flatMap((edge) => [edge.source, edge.target]))
  if (edgeTypeFilter.value !== 'all' || confidenceFilter.value !== 'all' || scriptFilter.value || collapsedScripts.value.size) {
    nodes = nodes.filter((node) => connected.has(node.id))
  }
  if (largeGraphLimited.value) {
    const visibleIds = new Set(nodes.slice(0, 300).map((node) => node.id))
    nodes = nodes.filter((node) => visibleIds.has(node.id))
    edges = edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
  }
  return { nodes, edges }
})

const graphData = computed(() => {
  const base = filteredBase.value
  const selected = matchedNodeId.value
  if (!selected || focusMode.value === 'all') {
    return {
      nodes: base.nodes.map((node) => ({ ...node, data: { ...node.data, matched: selected && node.id === selected } })),
      edges: base.edges,
    }
  }
  const adjacency = new Map()
  const reverse = new Map()
  base.edges.forEach((edge) => {
    adjacency.set(edge.source, [...(adjacency.get(edge.source) || []), edge.target])
    reverse.set(edge.target, [...(reverse.get(edge.target) || []), edge.source])
  })
  const visible = new Set([selected])
  const queue = [...(focusMode.value === 'upstream' ? reverse.get(selected) || [] : adjacency.get(selected) || [])]
  while (queue.length) {
    const current = queue.shift()
    if (!current || visible.has(current)) continue
    visible.add(current)
    queue.push(...(focusMode.value === 'upstream' ? reverse.get(current) || [] : adjacency.get(current) || []))
  }
  return {
    nodes: base.nodes
      .filter((node) => visible.has(node.id))
      .map((node) => ({ ...node, data: { ...node.data, matched: node.id === selected } })),
    edges: base.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target)),
  }
})

const selectedNodeDetails = computed(() => {
  if (!selectedItem.value || selectedItem.value.type !== 'node') return null
  const id = selectedItem.value.id
  return {
    id,
    upstream: allGraphData.value.edges.filter((edge) => edge.target === id).map((edge) => edge.source),
    downstream: allGraphData.value.edges.filter((edge) => edge.source === id).map((edge) => edge.target),
    edges: props.edges.filter((edge) => edge.source_table === id || edge.target_table === id),
  }
})

const selectedEdgeDetails = computed(() => {
  if (!selectedItem.value || selectedItem.value.type !== 'edge') return []
  return edgeDetails.value.get(selectedItem.value.id) || []
})

const toggleSet = (target, value) => {
  const next = new Set(target.value)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  target.value = next
}

const nextMatch = () => {
  if (!searchMatches.value.length) return
  matchIndex.value = (matchIndex.value + 1) % searchMatches.value.length
}

const exportPng = async () => {
  if (!graph) return
  const url = await graph.toDataURL({ type: 'image/png' })
  const link = document.createElement('a')
  link.href = url
  link.download = 'lineage-graph.png'
  link.click()
}

const exportJson = () => {
  const blob = new Blob([JSON.stringify(graphData.value, null, 2)], { type: 'application/json' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = 'lineage-graph.json'
  link.click()
  URL.revokeObjectURL(link.href)
}

const pickId = (event) => event?.target?.id || event?.target?.attributes?.id || event?.item?.id || event?.itemId || ''

const renderGraph = async () => {
  await nextTick()
  if (!graphEl.value) return
  if (graph) {
    graph.destroy()
    graph = null
  }
  if (!graphData.value.nodes.length) return
  graph = new Graph({
    container: graphEl.value,
    autoFit: 'view',
    height: 520,
    data: graphData.value,
    layout: { type: 'dagre', rankdir: 'LR', nodesep: 26, ranksep: 90 },
    node: {
      style: (datum) => ({
        labelText: datum.id,
        labelWordWrap: true,
        labelMaxWidth: 190,
        size: [210, 42],
        radius: 8,
        fill: datum.data?.role === 'target' ? '#dcfce7' : datum.data?.role === 'dependency' ? '#fffbeb' : '#ffffff',
        stroke: datum.data?.matched ? '#ef4444' : datum.data?.role === 'target' ? '#86efac' : datum.data?.role === 'dependency' ? '#facc15' : '#cbd5e1',
        lineWidth: datum.data?.matched ? 3 : 1,
        lineDash: datum.data?.role === 'dependency' ? [4, 4] : undefined,
      }),
    },
    edge: {
      style: (datum) => ({
        stroke: datum.data?.dependency ? '#d97706' : '#2563eb',
        lineDash: datum.data?.dependency ? [4, 4] : undefined,
        endArrow: true,
      }),
    },
    behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element'],
    plugins: [{ type: 'minimap', size: [150, 96] }],
  })
  graph.on('node:click', (event) => {
    const id = pickId(event)
    if (id) selectedItem.value = { type: 'node', id }
  })
  graph.on('edge:click', (event) => {
    const id = pickId(event)
    if (id) selectedItem.value = { type: 'edge', id }
  })
  await graph.render()
}

watch(searchText, () => { matchIndex.value = 0 })
watch(
  () => [
    props.groups,
    props.edges,
    searchText.value,
    focusMode.value,
    roleFilter.value,
    edgeTypeFilter.value,
    confidenceFilter.value,
    scriptFilter.value,
    schemaFilter.value,
    [...collapsedSchemas.value].join('|'),
    [...collapsedScripts.value].join('|'),
    largeGraphExpanded.value,
  ],
  renderGraph,
  { deep: true },
)
onMounted(renderGraph)
onBeforeUnmount(() => graph?.destroy())
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
    <div class="grid gap-3 border-b border-slate-200 bg-white p-3">
      <div class="flex flex-wrap items-center gap-2">
        <input v-model="searchText" class="min-w-[220px] flex-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm" placeholder="搜索表名">
        <button class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50" @click="nextMatch">下一个 {{ searchMatches.length ? `${matchIndex + 1}/${searchMatches.length}` : '' }}</button>
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600">
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'all' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'all'">全图</button>
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'upstream' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'upstream'">上游</button>
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'downstream' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'downstream'">下游</button>
        </div>
        <button class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50" @click="exportPng">导出 PNG</button>
        <button class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50" @click="exportJson">导出 JSON</button>
      </div>
      <div class="grid gap-2 md:grid-cols-5">
        <select v-model="roleFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm"><option value="all">全部节点</option><option value="source">来源表</option><option value="target">目标表</option><option value="dependency">条件依赖</option></select>
        <select v-model="edgeTypeFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm"><option value="all">全部边类型</option><option v-for="item in edgeTypes" :key="item" :value="item">{{ item }}</option></select>
        <select v-model="confidenceFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm"><option value="all">全部可信度</option><option v-for="item in confidences" :key="item" :value="item">{{ item }}</option></select>
        <select v-model="scriptFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm"><option value="">全部脚本</option><option v-for="item in scripts" :key="item" :value="item">{{ item }}</option></select>
        <select v-model="schemaFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm"><option value="all">全部 schema</option><option v-for="item in schemas" :key="item" :value="item">{{ item }}</option></select>
      </div>
      <div v-if="schemas.length || scripts.length" class="grid gap-2 text-xs text-slate-600 lg:grid-cols-2">
        <div v-if="schemas.length" class="flex flex-wrap gap-2">
          <span class="font-bold">折叠 schema</span>
          <button v-for="item in schemas" :key="item" class="rounded-full px-2 py-1" :class="collapsedSchemas.has(item) ? 'bg-slate-700 text-white' : 'bg-slate-100'" @click="toggleSet(collapsedSchemas, item)">{{ item }}</button>
        </div>
        <div v-if="scripts.length" class="flex flex-wrap gap-2">
          <span class="font-bold">折叠脚本</span>
          <button v-for="item in scripts" :key="item" class="rounded-full px-2 py-1" :class="collapsedScripts.has(item) ? 'bg-slate-700 text-white' : 'bg-slate-100'" @click="toggleSet(collapsedScripts, item)">{{ item }}</button>
        </div>
      </div>
      <div v-if="largeGraphLimited" class="flex items-center justify-between rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
        <span>当前图超过 300 个节点，已先渲染前 300 个节点；可搜索或展开全图。</span>
        <button class="font-bold text-amber-900" @click="largeGraphExpanded = true">展开全图</button>
      </div>
    </div>
    <div class="grid lg:grid-cols-[minmax(0,1fr)_340px]">
      <div>
        <div v-if="!graphData.nodes.length" class="p-4 text-sm text-slate-500">暂无可绘制的血缘边</div>
        <div ref="graphEl" class="h-[520px] w-full"></div>
      </div>
      <aside class="border-t border-slate-200 bg-white p-4 text-sm lg:border-l lg:border-t-0">
        <h3 class="mb-3 font-bold text-slate-800">详情</h3>
        <div v-if="selectedNodeDetails" class="space-y-3">
          <div><span class="text-slate-400">节点</span><p class="font-bold text-slate-800">{{ selectedNodeDetails.id }}</p></div>
          <div><span class="text-slate-400">上游</span><p>{{ selectedNodeDetails.upstream.join(', ') || '无' }}</p></div>
          <div><span class="text-slate-400">下游</span><p>{{ selectedNodeDetails.downstream.join(', ') || '无' }}</p></div>
          <div><span class="text-slate-400">相关字段映射</span><p>{{ selectedNodeDetails.edges.length }} 条</p></div>
        </div>
        <div v-else-if="selectedEdgeDetails.length" class="space-y-3">
          <div v-for="item in selectedEdgeDetails" :key="item.source_table + item.target_table + item.statement_index + item.edge_type" class="rounded-xl bg-slate-50 p-3">
            <p class="font-bold text-slate-800">{{ item.source_table }} → {{ item.target_table }}</p>
            <p class="mt-1 text-slate-500">{{ item.edge_type || '字段来源' }} · 语句 {{ item.statement_index || '-' }} · {{ item.confidence || 'high' }}</p>
            <p class="mt-2 break-all">来源字段：{{ (item.source_columns || []).join(', ') || '-' }}</p>
            <p class="break-all">目标字段：{{ (item.target_columns || []).join(', ') || '-' }}</p>
            <p class="mt-2 break-all text-slate-500">{{ item.reason || '-' }}</p>
          </div>
        </div>
        <p v-else class="text-slate-500">点击图中的节点或边查看上下文。</p>
      </aside>
    </div>
  </div>
</template>
