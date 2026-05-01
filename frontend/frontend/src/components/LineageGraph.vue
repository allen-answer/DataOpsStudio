<script setup>
import { Graph } from '@antv/g6'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  groups: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
})

const PREFS_KEY = 'lineage-graph-prefs-v1'
const SPACING_PRESETS = {
  compact: { nodesep: 16, ranksep: 60 },
  normal: { nodesep: 26, ranksep: 90 },
  relaxed: { nodesep: 44, ranksep: 160 },
}
const loadPrefs = () => {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') || {}
  } catch {
    return {}
  }
}
const initialPrefs = loadPrefs()

const graphEl = ref(null)
const searchText = ref('')
const matchIndex = ref(0)
const focusMode = ref('neighborhood')
const hopDepth = ref(1)
const clickedFocalId = ref('')
const roleFilter = ref('all')
const edgeTypeFilter = ref('all')
const confidenceFilter = ref('all')
const scriptFilter = ref('')
const schemaFilter = ref('all')
const selectedItem = ref(null)
const collapsedSchemas = ref(new Set())
const collapsedScripts = ref(new Set())
const largeGraphExpanded = ref(false)
const layoutDir = ref(initialPrefs.layoutDir === 'TB' ? 'TB' : 'LR')
const spacingPreset = ref(SPACING_PRESETS[initialPrefs.spacingPreset] ? initialPrefs.spacingPreset : 'normal')
const viewMode = ref('graph')
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

const COMBO_PREFIX = '__combo:'

const projectedBase = computed(() => {
  const base = filteredBase.value
  if (!collapsedSchemas.value.size) return base
  const isCollapsed = (schema) => collapsedSchemas.value.has(schema)
  const projectId = (id) => {
    const node = base.nodes.find((n) => n.id === id)
    if (node && isCollapsed(node.data.schema)) return `${COMBO_PREFIX}${node.data.schema}`
    return id
  }
  const remappedNodes = new Map()
  const comboCounts = new Map()
  for (const node of base.nodes) {
    if (isCollapsed(node.data.schema)) {
      const id = `${COMBO_PREFIX}${node.data.schema}`
      comboCounts.set(id, (comboCounts.get(id) || 0) + 1)
      if (!remappedNodes.has(id)) {
        remappedNodes.set(id, {
          id,
          data: { role: 'combo', schema: node.data.schema, isCombo: true },
        })
      }
    } else if (!remappedNodes.has(node.id)) {
      remappedNodes.set(node.id, node)
    }
  }
  for (const [id, count] of comboCounts) {
    remappedNodes.get(id).data.count = count
  }
  const remappedEdges = new Map()
  for (const edge of base.edges) {
    const source = projectId(edge.source)
    const target = projectId(edge.target)
    if (source === target) continue
    const key = `${source}|||${target}`
    const existing = remappedEdges.get(key)
    if (existing) {
      existing.data.aggregatedCount += 1
      existing.data.details = existing.data.details.concat(edge.data.details)
      if (edge.data.dependency) existing.data.dependency = true
    } else {
      remappedEdges.set(key, {
        id: key,
        source,
        target,
        data: {
          ...edge.data,
          details: [...edge.data.details],
          aggregatedCount: 1,
        },
      })
    }
  }
  return { nodes: Array.from(remappedNodes.values()), edges: Array.from(remappedEdges.values()) }
})

const adjacencyMaps = computed(() => {
  const out = new Map()
  const inn = new Map()
  projectedBase.value.edges.forEach((edge) => {
    out.set(edge.source, [...(out.get(edge.source) || []), edge.target])
    inn.set(edge.target, [...(inn.get(edge.target) || []), edge.source])
  })
  return { out, inn }
})

const autoFocalId = computed(() => {
  const nodes = projectedBase.value.nodes
  if (nodes.length <= 30) return ''
  const degree = new Map()
  projectedBase.value.edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1)
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1)
  })
  let bestId = nodes[0]?.id || ''
  let bestDeg = -1
  for (const node of nodes) {
    if (node.data.isCombo) continue
    const d = degree.get(node.id) || 0
    if (d > bestDeg) { bestDeg = d; bestId = node.id }
  }
  return bestId
})

const effectiveFocalId = computed(() => {
  const present = (id) => id && projectedBase.value.nodes.some((node) => node.id === id)
  if (present(clickedFocalId.value)) return clickedFocalId.value
  if (matchedNodeId.value && present(matchedNodeId.value)) return matchedNodeId.value
  if (focusMode.value !== 'all') return autoFocalId.value
  return ''
})

const bfsLimited = (start, getNeighbors, depth) => {
  const visited = new Set([start])
  let frontier = [start]
  for (let d = 0; d < depth; d++) {
    const next = []
    for (const node of frontier) {
      for (const neighbor of getNeighbors(node) || []) {
        if (visited.has(neighbor)) continue
        visited.add(neighbor)
        next.push(neighbor)
      }
    }
    if (!next.length) break
    frontier = next
  }
  return visited
}

const graphData = computed(() => {
  const base = projectedBase.value
  const focal = effectiveFocalId.value
  if (!focal || focusMode.value === 'all') {
    return {
      nodes: base.nodes.map((node) => ({ ...node, data: { ...node.data, matched: focal && node.id === focal } })),
      edges: base.edges,
    }
  }
  const { out, inn } = adjacencyMaps.value
  const visible = new Set([focal])
  if (focusMode.value !== 'downstream') {
    bfsLimited(focal, (n) => inn.get(n), hopDepth.value).forEach((id) => visible.add(id))
  }
  if (focusMode.value !== 'upstream') {
    bfsLimited(focal, (n) => out.get(n), hopDepth.value).forEach((id) => visible.add(id))
  }
  return {
    nodes: base.nodes
      .filter((node) => visible.has(node.id))
      .map((node) => ({ ...node, data: { ...node.data, matched: node.id === focal } })),
    edges: base.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target)),
  }
})

const visibleStats = computed(() => ({
  visible: graphData.value.nodes.length,
  total: projectedBase.value.nodes.length,
}))

const tableRows = computed(() => {
  const base = projectedBase.value
  const focal = effectiveFocalId.value
  const findNode = (id) => base.nodes.find((node) => node.id === id)
  if (!focal) {
    return base.nodes.map((node) => ({
      id: node.id,
      schema: node.data.schema,
      hops: '-',
      direction: '全图',
      upstream: base.edges.filter((edge) => edge.target === node.id).length,
      downstream: base.edges.filter((edge) => edge.source === node.id).length,
      isCombo: !!node.data.isCombo,
    })).sort((a, b) => a.id.localeCompare(b.id))
  }
  const { out, inn } = adjacencyMaps.value
  const focalNode = findNode(focal)
  const rows = [{
    id: focal,
    schema: focalNode?.data.schema || '-',
    hops: 0,
    direction: '聚焦',
    upstream: (inn.get(focal) || []).length,
    downstream: (out.get(focal) || []).length,
    isCombo: !!focalNode?.data.isCombo,
  }]
  const traverse = (getNeighbors, label) => {
    const visited = new Set([focal])
    let frontier = [focal]
    let depth = 0
    while (frontier.length && depth < 10) {
      depth += 1
      const next = []
      for (const id of frontier) {
        for (const neighbor of getNeighbors(id) || []) {
          if (visited.has(neighbor)) continue
          visited.add(neighbor)
          next.push(neighbor)
          const node = findNode(neighbor)
          rows.push({
            id: neighbor,
            schema: node?.data.schema || '-',
            hops: depth,
            direction: label,
            upstream: (inn.get(neighbor) || []).length,
            downstream: (out.get(neighbor) || []).length,
            isCombo: !!node?.data.isCombo,
          })
        }
      }
      frontier = next
    }
  }
  traverse((id) => inn.get(id), '上游')
  traverse((id) => out.get(id), '下游')
  return rows
})

const tableSuggested = computed(() => projectedBase.value.nodes.length > 100)

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
  if (graph) {
    graph.destroy()
    graph = null
  }
  if (viewMode.value !== 'graph') return
  if (!graphEl.value) return
  if (!graphData.value.nodes.length) return
  graph = new Graph({
    container: graphEl.value,
    autoFit: 'view',
    height: 520,
    data: graphData.value,
    layout: { type: 'dagre', rankdir: layoutDir.value, ...SPACING_PRESETS[spacingPreset.value] },
    node: {
      style: (datum) => {
        const isCombo = datum.data?.isCombo
        const label = isCombo ? `${datum.data.schema} (${datum.data.count} 表)` : datum.id
        return {
          labelText: label,
          labelWordWrap: true,
          labelMaxWidth: isCombo ? 220 : 190,
          labelFontWeight: isCombo ? 700 : 400,
          size: isCombo ? [240, 56] : [210, 42],
          radius: isCombo ? 12 : 8,
          fill: isCombo ? '#ede9fe' : datum.data?.role === 'target' ? '#dcfce7' : datum.data?.role === 'dependency' ? '#fffbeb' : '#ffffff',
          stroke: datum.data?.matched ? '#ef4444' : isCombo ? '#a78bfa' : datum.data?.role === 'target' ? '#86efac' : datum.data?.role === 'dependency' ? '#facc15' : '#cbd5e1',
          lineWidth: datum.data?.matched ? 3 : isCombo ? 2 : 1,
          lineDash: !isCombo && datum.data?.role === 'dependency' ? [4, 4] : undefined,
        }
      },
    },
    edge: {
      style: (datum) => {
        const count = datum.data?.aggregatedCount || 1
        return {
          stroke: datum.data?.dependency ? '#d97706' : '#2563eb',
          lineDash: datum.data?.dependency ? [4, 4] : undefined,
          lineWidth: count > 1 ? Math.min(1 + Math.log2(count), 4) : 1,
          endArrow: true,
          labelText: count > 1 ? `×${count}` : '',
          labelFill: '#475569',
          labelFontSize: 10,
          labelBackground: count > 1,
          labelBackgroundFill: '#ffffff',
          labelBackgroundOpacity: 0.85,
        }
      },
    },
    behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element'],
    plugins: [{ type: 'minimap', size: [150, 96] }],
  })
  graph.on('node:click', (event) => {
    const id = pickId(event)
    if (!id) return
    if (id.startsWith(COMBO_PREFIX)) {
      toggleSet(collapsedSchemas, id.slice(COMBO_PREFIX.length))
      return
    }
    selectedItem.value = { type: 'node', id }
    clickedFocalId.value = id
  })
  graph.on('edge:click', (event) => {
    const id = pickId(event)
    if (id) selectedItem.value = { type: 'edge', id }
  })
  await graph.render()
}

watch(searchText, () => { matchIndex.value = 0 })
watch(() => [props.groups, props.edges], () => { clickedFocalId.value = '' }, { deep: true })
watch([layoutDir, spacingPreset], ([dir, preset]) => {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ layoutDir: dir, spacingPreset: preset }))
  } catch {}
})
watch(matchedNodeId, (id) => {
  if (!id || !collapsedSchemas.value.size) return
  const original = allGraphData.value.nodes.find((node) => node.id === id)
  if (original && collapsedSchemas.value.has(original.data.schema)) {
    toggleSet(collapsedSchemas, original.data.schema)
  }
})
watch(
  () => [
    props.groups,
    props.edges,
    searchText.value,
    focusMode.value,
    hopDepth.value,
    clickedFocalId.value,
    roleFilter.value,
    edgeTypeFilter.value,
    confidenceFilter.value,
    scriptFilter.value,
    schemaFilter.value,
    [...collapsedSchemas.value].join('|'),
    [...collapsedScripts.value].join('|'),
    largeGraphExpanded.value,
    layoutDir.value,
    spacingPreset.value,
    viewMode.value,
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
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600" title="视图模式">
          <button class="rounded-md px-3 py-1.5" :class="viewMode === 'graph' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="viewMode = 'graph'">图</button>
          <button class="rounded-md px-3 py-1.5" :class="viewMode === 'table' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="viewMode = 'table'">表</button>
        </div>
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600">
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'neighborhood' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'neighborhood'">周边</button>
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'upstream' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'upstream'">上游</button>
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'downstream' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'downstream'">下游</button>
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'all' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'all'">全图</button>
        </div>
        <label class="flex items-center gap-2 text-sm text-slate-600" :class="focusMode === 'all' ? 'opacity-50' : ''">
          <span class="font-bold">跳数</span>
          <input v-model.number="hopDepth" type="range" min="1" max="5" step="1" :disabled="focusMode === 'all'" class="w-24">
          <span class="w-4 font-mono">{{ hopDepth }}</span>
        </label>
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600" title="布局方向">
          <button class="rounded-md px-3 py-1.5" :class="layoutDir === 'LR' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="layoutDir = 'LR'">横向</button>
          <button class="rounded-md px-3 py-1.5" :class="layoutDir === 'TB' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="layoutDir = 'TB'">纵向</button>
        </div>
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600" title="节点间距">
          <button class="rounded-md px-3 py-1.5" :class="spacingPreset === 'compact' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="spacingPreset = 'compact'">紧凑</button>
          <button class="rounded-md px-3 py-1.5" :class="spacingPreset === 'normal' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="spacingPreset = 'normal'">标准</button>
          <button class="rounded-md px-3 py-1.5" :class="spacingPreset === 'relaxed' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="spacingPreset = 'relaxed'">宽松</button>
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
      <div v-if="effectiveFocalId && focusMode !== 'all'" class="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-800">
        <span>聚焦 <code class="rounded bg-white px-1.5 py-0.5 font-mono text-blue-700">{{ effectiveFocalId }}</code> · {{ focusMode === 'upstream' ? '上游' : focusMode === 'downstream' ? '下游' : '周边' }} {{ hopDepth }} 跳 · 显示 {{ visibleStats.visible }}/{{ visibleStats.total }} 节点</span>
        <div class="flex gap-2">
          <button v-if="hopDepth < 5" class="font-bold text-blue-900 hover:underline" @click="hopDepth = Math.min(5, hopDepth + 1)">扩到 {{ hopDepth + 1 }} 跳</button>
          <button class="font-bold text-blue-900 hover:underline" @click="focusMode = 'all'; clickedFocalId = ''">显示全图</button>
        </div>
      </div>
      <div v-if="largeGraphLimited" class="flex items-center justify-between rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
        <span>当前图超过 300 个节点，已先渲染前 300 个节点；可搜索或展开全图。</span>
        <button class="font-bold text-amber-900" @click="largeGraphExpanded = true">展开全图</button>
      </div>
      <div v-if="tableSuggested && viewMode === 'graph'" class="flex items-center justify-between rounded-lg bg-violet-50 px-3 py-2 text-sm text-violet-800">
        <span>节点超过 100，建议切到「表」视图做影响分析更清晰。</span>
        <button class="font-bold text-violet-900 hover:underline" @click="viewMode = 'table'">切到表视图</button>
      </div>
    </div>
    <div class="grid lg:grid-cols-[minmax(0,1fr)_340px]">
      <div>
        <div v-if="viewMode === 'graph'">
          <div v-if="!graphData.nodes.length" class="p-4 text-sm text-slate-500">暂无可绘制的血缘边</div>
          <div ref="graphEl" class="h-[520px] w-full"></div>
        </div>
        <div v-else class="max-h-[520px] overflow-auto">
          <table class="w-full text-sm">
            <thead class="sticky top-0 bg-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-600">
              <tr>
                <th class="px-3 py-2">表 / Combo</th>
                <th class="px-3 py-2">Schema</th>
                <th class="px-3 py-2">方向</th>
                <th class="px-3 py-2">跳数</th>
                <th class="px-3 py-2 text-right">上游</th>
                <th class="px-3 py-2 text-right">下游</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in tableRows" :key="row.id" class="cursor-pointer border-b border-slate-100 hover:bg-slate-50" @click="row.isCombo ? toggleSet(collapsedSchemas, row.schema) : (clickedFocalId = row.id, selectedItem = { type: 'node', id: row.id })">
                <td class="px-3 py-2 font-mono text-slate-800" :class="row.isCombo ? 'text-violet-700' : ''">{{ row.id }}</td>
                <td class="px-3 py-2 text-slate-600">{{ row.schema }}</td>
                <td class="px-3 py-2"><span class="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-bold" :class="row.direction === '聚焦' ? 'bg-blue-100 text-blue-700' : row.direction === '上游' ? 'bg-emerald-100 text-emerald-700' : row.direction === '下游' ? 'bg-amber-100 text-amber-700' : 'text-slate-600'">{{ row.direction }}</span></td>
                <td class="px-3 py-2 text-slate-600">{{ row.hops }}</td>
                <td class="px-3 py-2 text-right text-slate-600">{{ row.upstream }}</td>
                <td class="px-3 py-2 text-right text-slate-600">{{ row.downstream }}</td>
              </tr>
              <tr v-if="!tableRows.length"><td colspan="6" class="p-4 text-center text-slate-500">暂无血缘节点</td></tr>
            </tbody>
          </table>
        </div>
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
