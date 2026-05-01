<script setup>
import { Graph } from '@antv/g6'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  groups: { type: Array, default: () => [] },
})

const graphEl = ref(null)
const searchText = ref('')
const focusMode = ref('all')
let graph = null

const allGraphData = computed(() => {
  const nodes = new Map()
  const edges = []
  props.groups.forEach((group) => {
    const target = group.target_table
    if (!target) return
    nodes.set(target, { id: target, data: { role: 'target' } })
    ;(group.source_tables || []).forEach((source) => {
      nodes.set(source, { id: source, data: { role: 'source' } })
      edges.push({ source, target })
    })
    ;(group.dependency_tables || []).forEach((source) => {
      nodes.set(source, { id: source, data: { role: 'dependency' } })
      edges.push({ source, target, data: { dependency: true } })
    })
  })
  return { nodes: Array.from(nodes.values()), edges }
})

const matchedNodeId = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  if (!keyword) return ''
  return allGraphData.value.nodes.find((node) => node.id.toLowerCase().includes(keyword))?.id || ''
})

const graphData = computed(() => {
  const base = allGraphData.value
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

const exportPng = async () => {
  if (!graph) return
  const url = await graph.toDataURL({ type: 'image/png' })
  const link = document.createElement('a')
  link.href = url
  link.download = 'lineage-graph.png'
  link.click()
}

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
    height: 460,
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
    plugins: [{ type: 'minimap', size: [140, 92] }],
  })
  await graph.render()
}

watch(() => [props.groups, searchText.value, focusMode.value], renderGraph, { deep: true })
onMounted(renderGraph)
onBeforeUnmount(() => graph?.destroy())
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
    <div class="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white p-3">
      <input v-model="searchText" class="min-w-[220px] flex-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm" placeholder="搜索表名">
      <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600">
        <button class="rounded-md px-3 py-1.5" :class="focusMode === 'all' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'all'">全图</button>
        <button class="rounded-md px-3 py-1.5" :class="focusMode === 'upstream' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'upstream'">上游</button>
        <button class="rounded-md px-3 py-1.5" :class="focusMode === 'downstream' ? 'bg-white text-blue-600 shadow-sm' : ''" @click="focusMode = 'downstream'">下游</button>
      </div>
      <button class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50" @click="exportPng">导出 PNG</button>
    </div>
    <div v-if="!graphData.nodes.length" class="p-4 text-sm text-slate-500">暂无可绘制的血缘边</div>
    <div ref="graphEl" class="h-[460px] w-full"></div>
  </div>
</template>
