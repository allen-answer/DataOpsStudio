<script setup>
// G6 引擎实现。数据派生 / 筛选 / 聚焦逻辑全在 composables/useLineageGraphData.js，
// 这里只负责 G6 渲染、交互绑定、布局/间距 prefs。
import { Graph } from '@antv/g6'
import { nextTick, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLineageGraphData } from '../composables/useLineageGraphData.js'

// S5 PR12：把 supplemental edge type 技术名 → 友好标签
const { t, te } = useI18n()
function edgeTypeLabel(type) {
  if (!type) return ''
  const key = `lineagePanel.common.edgeTypes.${type}`
  return te(key) ? t(key) : type
}

const props = defineProps({
  groups: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
  // Phase 10 #3 enhancement：节点徽章 —— {table_name: [{aspect_type, value, ...}]}
  // emoji 前缀方案，对 G6 / Cytoscape 都不需要 engine-specific 改动
  aspectsByTable: { type: Object, default: () => ({}) },
})

// Phase 10 #3 enhancement：根据表的 aspects 生成 emoji 前缀。三类高优 type
// 用一组直观符号；用户自定义类型不展示徽章（避免视觉杂乱）
const ASPECT_BADGES = {
  pii: '🔒',     // 红 PII
  sla: '⏰',     // 时间 SLA
  owner: '👤',   // 头像 owner
  sensitive: '⚠️',
}
function badgePrefix(name) {
  const aspects = props.aspectsByTable?.[name]
  if (!aspects?.length) return ''
  // 去重（同 type 多 aspect 只显一个 emoji）+ 按固定顺序避免抖动
  const seen = new Set()
  for (const a of aspects) {
    if (ASPECT_BADGES[a.aspect_type]) seen.add(a.aspect_type)
  }
  if (!seen.size) return ''
  return ['pii', 'sla', 'sensitive', 'owner'].filter(t => seen.has(t)).map(t => ASPECT_BADGES[t]).join('') + ' '
}

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

const lineage = useLineageGraphData(toRef(props, 'groups'), toRef(props, 'edges'))
const {
  searchText, matchIndex, focusMode, hopDepth, clickedFocalId,
  roleFilter, edgeTypeFilter, confidenceFilter, scriptFilter, schemaFilter,
  collapsedSchemas, collapsedScripts, largeGraphExpanded, selectedItem,
  scriptSearch,
  schemas, scripts, edgeTypes, confidences,
  schemaCounts, scriptCounts,
  searchMatches, filteredScriptList,
  largeGraphLimited,
  effectiveFocalId, graphData, visibleStats,
  tableRows, tableSuggested,
  selectedNodeDetails, selectedEdgeDetails,
  toggleSet, nextMatch, clearCollapsedScripts, keepOnlyScript, hideAllScripts, basename,
  COMBO_PREFIX,
} = lineage

const graphEl = ref(null)
const layoutDir = ref(initialPrefs.layoutDir === 'TB' ? 'TB' : 'LR')
const spacingPreset = ref(SPACING_PRESETS[initialPrefs.spacingPreset] ? initialPrefs.spacingPreset : 'normal')
const viewMode = ref('graph')
const scriptListOpen = ref(false)
let graph = null

const pickId = (event) => event?.target?.id || event?.target?.attributes?.id || event?.item?.id || event?.itemId || ''

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
        const baseLabel = isCombo ? `${datum.data.schema} (${datum.data.count} 表)` : datum.id
        const label = isCombo ? baseLabel : badgePrefix(datum.id) + baseLabel
        return {
          labelText: label,
          labelWordWrap: true,
          labelMaxWidth: isCombo ? 220 : 190,
          labelFontWeight: isCombo ? 700 : 400,
          size: isCombo ? [240, 56] : [210, 42],
          radius: isCombo ? 12 : 8,
          fill: isCombo
            ? '#ede9fe'
            : datum.data?.role === 'target'
              ? '#dcfce7'
              : datum.data?.role === 'dependency'
                ? '#fffbeb'
                : '#ffffff',
          stroke: datum.data?.matched
            ? '#ef4444'
            : isCombo
              ? '#a78bfa'
              : datum.data?.role === 'target'
                ? '#86efac'
                : datum.data?.role === 'dependency'
                  ? '#facc15'
                  : '#cbd5e1',
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

watch([layoutDir, spacingPreset], ([dir, preset]) => {
  try {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ ...loadPrefs(), layoutDir: dir, spacingPreset: preset }),
    )
  } catch {}
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
        <button class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50" @click="nextMatch">下一个 {{ searchMatches.length ? `${(matchIndex % searchMatches.length) + 1}/${searchMatches.length}` : '' }}</button>
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
        <select v-model="edgeTypeFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm"><option value="all">全部边类型</option><option v-for="item in edgeTypes" :key="item" :value="item">{{ edgeTypeLabel(item) }}</option></select>
        <select v-model="confidenceFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm"><option value="all">全部可信度</option><option v-for="item in confidences" :key="item" :value="item">{{ item }}</option></select>
        <select v-model="scriptFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm" :title="scriptFilter || ''"><option value="">全部脚本</option><option v-for="item in scripts" :key="item" :value="item">{{ basename(item) }}</option></select>
        <select v-model="schemaFilter" class="rounded-lg border-none bg-slate-50 px-3 py-2 text-sm"><option value="all">全部 schema</option><option v-for="item in schemas" :key="item" :value="item">{{ item }}</option></select>
      </div>
      <div v-if="schemas.length || scripts.length" class="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-xs text-slate-600">
        <p class="muted text-[11px]">
          聚合：把同一 schema / 同一脚本的节点合并展示，减少噪音；点击高亮的标签可恢复展开
        </p>
        <div v-if="schemas.length" class="flex flex-wrap items-center gap-1.5">
          <span class="font-semibold text-slate-700">按 schema 聚合</span>
          <button
            v-for="item in schemas" :key="item"
            class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 transition"
            :class="collapsedSchemas.has(item)
              ? 'border-primary bg-primary text-white'
              : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-100'"
            @click="toggleSet(collapsedSchemas, item)"
          >
            <span>{{ item }}</span>
            <span class="text-[10px]" :class="collapsedSchemas.has(item) ? 'text-white/80' : 'text-slate-400'">{{ schemaCounts.get(item) || 0 }}</span>
          </button>
        </div>
        <div v-if="scripts.length" class="relative">
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="font-semibold text-slate-700">脚本过滤</span>
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50"
              @click="scriptListOpen = !scriptListOpen"
            >
              <span>{{ scripts.length }} 个脚本</span>
              <span v-if="collapsedScripts.size" class="rounded bg-primary-light px-1 text-primary">已隐藏 {{ collapsedScripts.size }}</span>
              <span class="text-slate-400">{{ scriptListOpen ? '收起' : '展开' }}</span>
            </button>
            <button
              v-if="collapsedScripts.size"
              type="button"
              class="text-[11px] text-slate-500 underline hover:text-slate-700"
              @click="clearCollapsedScripts"
            >全部恢复</button>
          </div>
          <div
            v-if="scriptListOpen"
            class="absolute left-0 right-0 top-full z-10 mt-1 max-h-72 overflow-auto rounded-lg border border-slate-200 bg-white p-2 shadow-lg"
          >
            <input
              v-model="scriptSearch"
              type="text"
              placeholder="搜索脚本名"
              class="mb-2 w-full rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs"
            />
            <!-- 批量选择快捷：208 脚本时单点逐勾不现实 -->
            <div class="mb-2 flex items-center justify-end gap-2 px-1 text-[10px]">
              <button type="button" class="text-slate-500 underline hover:text-slate-700" @click="clearCollapsedScripts">全选</button>
              <span class="text-slate-300">|</span>
              <button type="button" class="text-slate-500 underline hover:text-slate-700" @click="hideAllScripts">全不选</button>
            </div>
            <ul class="space-y-0.5">
              <li v-for="f in filteredScriptList" :key="f">
                <label
                  class="group flex cursor-pointer items-center justify-between gap-2 rounded px-2 py-1 hover:bg-slate-50"
                  :title="f"
                >
                  <span class="flex items-center gap-2">
                    <input
                      type="checkbox"
                      :checked="!collapsedScripts.has(f)"
                      class="h-3.5 w-3.5"
                      @change="toggleSet(collapsedScripts, f)"
                    />
                    <span class="sql-font text-[11px] text-slate-700">{{ basename(f) }}</span>
                  </span>
                  <span class="flex items-center gap-2">
                    <button
                      type="button"
                      class="hidden rounded bg-primary-light px-1.5 py-0.5 text-[10px] font-medium text-primary hover:bg-primary hover:text-white group-hover:inline-block"
                      :title="`只保留 ${basename(f)}，隐藏其他所有脚本`"
                      @click.prevent.stop="keepOnlyScript(f)"
                    >仅此</button>
                    <span class="muted text-[10px]">{{ scriptCounts.get(f) || 0 }} 边</span>
                  </span>
                </label>
              </li>
              <li v-if="!filteredScriptList.length" class="muted px-2 py-2 text-center text-[11px]">无匹配脚本</li>
            </ul>
          </div>
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
          <div v-if="!graphData.nodes.length" class="p-6 text-center text-sm text-slate-500">
            <p>当前过滤条件下没有可绘制的血缘边</p>
            <p class="mt-1 text-[12px] text-slate-400">放宽顶部的角色 / 边类型 / 可信度筛选，或清空搜索词后重试。</p>
          </div>
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
              <tr v-if="!tableRows.length"><td colspan="6" class="p-6 text-center text-slate-500">没有命中节点 — 调整左上角的过滤条件，或在顶部搜索框输入表名重试</td></tr>
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
            <p class="mt-1 text-slate-500">{{ edgeTypeLabel(item.edge_type) || '字段来源' }} · 语句 {{ item.statement_index || '-' }} · {{ item.confidence || 'high' }}</p>
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
