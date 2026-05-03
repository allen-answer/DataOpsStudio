<script setup>
// 业务分组级 DAG —— 把 semantic_lineage.business_groups + grouped_edges
// 渲染成精简 G6 图。每个 group 是节点（size 按 table_count），跨分组依赖
// 是边（粗细按 edge_count + ×N 标签）。
//
// 跟 LineageGraph 的差别：
//   - LineageGraph：表级图，节点 = 表，边 = 写入关系；适合细查
//   - BusinessGroupDag：分组级图，节点 = 业务域，边 = 分组间数据流向；
//     适合"哪些业务流向哪些业务"的高层视图
//
// 复用 G6 已经在 g6-vendor chunk 里的代码 —— 这里不再下载新 vendor。
import { Graph } from '@antv/g6'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  groups: { type: Array, default: () => [] },        // semantic.business_groups
  groupedEdges: { type: Array, default: () => [] },  // semantic.grouped_edges
})
const emit = defineEmits(['focus-group'])

// 7 色循环（跟 SemanticLineagePanel 卡片配色一致）
const PALETTE = [
  { fill: '#dbeafe', stroke: '#3b82f6' }, // blue
  { fill: '#d1fae5', stroke: '#10b981' }, // emerald
  { fill: '#ede9fe', stroke: '#8b5cf6' }, // violet
  { fill: '#fef3c7', stroke: '#f59e0b' }, // amber
  { fill: '#cffafe', stroke: '#06b6d4' }, // cyan
  { fill: '#ffe4e6', stroke: '#f43f5e' }, // rose
  { fill: '#f1f5f9', stroke: '#64748b' }, // slate
]
const tone = (idx) => PALETTE[idx % PALETTE.length]

const containerEl = ref(null)
let graph = null
const selectedGroup = ref('')

// 节点 size 按 table_count（最小 56，最大 110）
function nodeRadius(tableCount) {
  if (!tableCount) return 28
  const r = 28 + Math.min(Math.log2(tableCount + 1) * 6, 28)
  return Math.round(r)
}

// 边粗细按 edge_count（最小 1，最大 6）
function edgeWidth(edgeCount) {
  if (!edgeCount) return 1
  return Math.min(1 + Math.log2(edgeCount + 1) * 1.2, 6)
}

const data = computed(() => {
  const indexByName = new Map()
  const nodes = (props.groups || []).map((g, i) => {
    indexByName.set(g.name, i)
    const t = tone(i)
    const r = nodeRadius(g.table_count || 0)
    return {
      id: g.name,
      data: {
        label: g.name,
        description: g.description || '',
        tableCount: g.table_count || 0,
        targetCount: g.target_count || 0,
        tables: g.tables || [],
        radius: r,
        fill: t.fill,
        stroke: t.stroke,
      },
    }
  })
  const edges = (props.groupedEdges || [])
    .filter(ge => indexByName.has(ge.source_group) && indexByName.has(ge.target_group))
    .map(ge => ({
      id: `${ge.source_group}__${ge.target_group}`,
      source: ge.source_group,
      target: ge.target_group,
      data: {
        edgeCount: ge.edge_count || 0,
        ruleOrder: ge.rule_order || 0,
      },
    }))
  return { nodes, edges }
})

const pickId = (event) => event?.target?.id || event?.target?.attributes?.id || event?.item?.id || event?.itemId || ''

const renderGraph = async () => {
  await nextTick()
  if (graph) {
    graph.destroy()
    graph = null
  }
  if (!containerEl.value || !data.value.nodes.length) return
  graph = new Graph({
    container: containerEl.value,
    autoFit: 'view',
    height: 380,
    data: data.value,
    layout: { type: 'dagre', rankdir: 'LR', nodesep: 30, ranksep: 80 },
    node: {
      type: 'circle',
      style: (datum) => ({
        size: datum.data.radius * 2,
        fill: datum.data.fill,
        stroke: datum.data.matched ? '#ef4444' : datum.data.stroke,
        lineWidth: datum.data.matched ? 3 : 1.5,
        labelText: datum.data.label,
        labelFill: '#1e293b',
        labelFontSize: 11,
        labelFontWeight: 600,
        labelPlacement: 'center',
        labelMaxWidth: datum.data.radius * 2 - 4,
        labelWordWrap: true,
      }),
    },
    edge: {
      type: 'line',
      style: (datum) => ({
        stroke: '#94a3b8',
        lineWidth: edgeWidth(datum.data?.edgeCount),
        endArrow: true,
        endArrowSize: 6,
        labelText: datum.data?.edgeCount ? `×${datum.data.edgeCount}` : '',
        labelFill: '#475569',
        labelFontSize: 10,
        labelBackground: !!datum.data?.edgeCount,
        labelBackgroundFill: '#ffffff',
        labelBackgroundOpacity: 0.85,
      }),
    },
    behaviors: ['drag-canvas', 'zoom-canvas'],
  })
  graph.on('node:click', (event) => {
    const id = pickId(event)
    if (id) {
      selectedGroup.value = id
      emit('focus-group', id)
    }
  })
  await graph.render()
}

watch(() => [props.groups, props.groupedEdges], renderGraph, { deep: true })
onMounted(renderGraph)
onBeforeUnmount(() => graph?.destroy())

const selectedDetails = computed(() => {
  if (!selectedGroup.value) return null
  return (props.groups || []).find(g => g.name === selectedGroup.value) || null
})
</script>

<template>
  <div v-if="(groups || []).length" class="overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
    <div class="grid lg:grid-cols-[minmax(0,1fr)_280px]">
      <div ref="containerEl" class="h-[380px] w-full"></div>
      <aside class="border-t border-slate-200 bg-white p-4 text-sm lg:border-l lg:border-t-0">
        <h3 class="mb-2 text-sm font-bold text-slate-800">分组详情</h3>
        <div v-if="selectedDetails" class="space-y-2">
          <div>
            <p class="text-base font-bold text-slate-800">{{ selectedDetails.name }}</p>
            <p v-if="selectedDetails.description" class="muted text-[11px]">
              {{ selectedDetails.description }}
            </p>
          </div>
          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="rounded bg-slate-50 px-2 py-1">
              <div class="muted text-[10px]">表数</div>
              <div class="font-bold text-slate-800">{{ selectedDetails.table_count }}</div>
            </div>
            <div class="rounded bg-slate-50 px-2 py-1">
              <div class="muted text-[10px]">写入数</div>
              <div class="font-bold text-slate-800">{{ selectedDetails.target_count || 0 }}</div>
            </div>
          </div>
          <div v-if="selectedDetails.tables?.length">
            <div class="muted mb-1 text-[10px]">包含表（前 8 张）</div>
            <div class="flex flex-wrap gap-1">
              <span
                v-for="t in selectedDetails.tables.slice(0, 8)" :key="t"
                class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] sql-font text-slate-700"
              >{{ t }}</span>
              <span
                v-if="selectedDetails.tables.length > 8"
                class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500"
              >+{{ selectedDetails.tables.length - 8 }}</span>
            </div>
          </div>
        </div>
        <p v-else class="muted text-xs">点击图中的分组查看详情</p>
      </aside>
    </div>
  </div>
  <div v-else class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
    <p class="text-sm">没有可绘制的业务分组</p>
    <p class="muted text-[11px]">配 config/lineage_group_rules.yml 后再分析才会出业务分组</p>
  </div>
</template>
