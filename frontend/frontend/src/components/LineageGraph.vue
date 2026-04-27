<script setup>
import { Graph } from '@antv/g6'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  groups: { type: Array, default: () => [] },
})

const graphEl = ref(null)
let graph = null

const graphData = computed(() => {
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
        stroke: datum.data?.role === 'target' ? '#86efac' : datum.data?.role === 'dependency' ? '#facc15' : '#cbd5e1',
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
  })
  await graph.render()
}

watch(() => props.groups, renderGraph, { deep: true })
onMounted(renderGraph)
onBeforeUnmount(() => graph?.destroy())
</script>

<template>
  <div class="min-h-[460px] overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
    <div v-if="!graphData.nodes.length" class="p-4 text-sm text-slate-500">暂无可绘制的血缘边</div>
    <div ref="graphEl" class="h-[460px] w-full"></div>
  </div>
</template>
