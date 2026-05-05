<script setup>
// 实验中的 Cytoscape.js 引擎实现，与 G6 引擎共享 useLineageGraphData composable。
// 核心差异：用 compound parent 节点表达 schema（替代 G6 的 combo collapse）。
//
// 这是为了验证 Cytoscape 在大图筛选 / 路径高亮 / 分组容器上的能力，
// 不替换 G6。两种引擎可在 LineageGraphPanel 切换。
import cytoscape from 'cytoscape'
import dagre from 'cytoscape-dagre'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLineageGraphData } from '../composables/useLineageGraphData.js'

cytoscape.use(dagre)

// S5 PR12：edge_type 友好标签
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
  aspectsByTable: { type: Object, default: () => ({}) },
})

// 跟 LineageGraph.vue 同一份 emoji 映射 —— 双引擎统一视觉
const ASPECT_BADGES = {
  pii: '🔒', sla: '⏰', owner: '👤', sensitive: '⚠️',
}
function badgePrefix(name) {
  const aspects = props.aspectsByTable?.[name]
  if (!aspects?.length) return ''
  const seen = new Set()
  for (const a of aspects) {
    if (ASPECT_BADGES[a.aspect_type]) seen.add(a.aspect_type)
  }
  if (!seen.size) return ''
  return ['pii', 'sla', 'sensitive', 'owner'].filter(t => seen.has(t)).map(t => ASPECT_BADGES[t]).join('') + ' '
}

const PREFS_KEY = 'lineage-graph-prefs-v1'
const SPACING_PRESETS = {
  compact: { nodeSep: 16, rankSep: 60 },
  normal: { nodeSep: 26, rankSep: 90 },
  relaxed: { nodeSep: 44, rankSep: 160 },
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
  collapsedScripts, largeGraphExpanded, selectedItem,
  scriptSearch,
  schemas, scripts, edgeTypes, confidences,
  schemaCounts, scriptCounts,
  searchMatches, filteredScriptList,
  largeGraphLimited, tableSuggested, tableRows,
  effectiveFocalId, cyData, cyAdjacency,
  selectedNodeDetails, selectedEdgeDetails,
  toggleSet, nextMatch, clearCollapsedScripts, basename,
} = lineage

const cyEl = ref(null)
const layoutDir = ref(initialPrefs.layoutDir === 'TB' ? 'TB' : 'LR')
const spacingPreset = ref(SPACING_PRESETS[initialPrefs.spacingPreset] ? initialPrefs.spacingPreset : 'normal')
const compoundBySchema = ref(initialPrefs.compoundBySchema !== false) // 默认开启 compound
const scriptListOpen = ref(false)
const viewMode = ref('graph')          // 'graph' | 'table' —— >100 节点逃生通道
const pathMode = ref(false)            // 路径高亮模式：选 from / to 两节点
const pathFrom = ref('')
const pathTo = ref('')
let cy = null

// 把 cyData 转成 cytoscape elements 格式：
// - compound 模式：每个 schema 出一个 parent 节点，table 节点 data.parent = schema:xxx
// - 非 compound：直接出 table 节点
const cyElements = computed(() => {
  const data = cyData.value
  const elements = []
  const seenSchemas = new Set()
  if (compoundBySchema.value) {
    for (const node of data.nodes) {
      const schema = node.data.schema || '(默认)'
      if (!seenSchemas.has(schema)) {
        seenSchemas.add(schema)
        elements.push({
          data: {
            id: `schema:${schema}`,
            label: schema,
            isParent: true,
            count: schemaCounts.value.get(schema) || 0,
          },
        })
      }
    }
  }
  for (const node of data.nodes) {
    elements.push({
      data: {
        id: node.id,
        label: badgePrefix(node.id) + node.id,
        role: node.data?.role || 'source',
        schema: node.data?.schema,
        matched: !!node.data?.matched,
        parent: compoundBySchema.value ? `schema:${node.data?.schema || '(默认)'}` : undefined,
      },
    })
  }
  for (const edge of data.edges) {
    elements.push({
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        dependency: !!edge.data?.dependency,
        count: edge.data?.aggregatedCount || 1,
      },
    })
  }
  return elements
})

const cyStyle = [
  {
    selector: 'node[?isParent]',
    style: {
      'background-color': '#ede9fe',
      'background-opacity': 0.5,
      'border-color': '#a78bfa',
      'border-width': 2,
      'border-style': 'dashed',
      'shape': 'round-rectangle',
      'label': 'data(label)',
      'font-size': 11,
      'font-weight': 700,
      'color': '#5b21b6',
      'text-valign': 'top',
      'text-halign': 'center',
      'padding': '14px',
      'text-margin-y': -6,
    },
  },
  {
    selector: 'node[!isParent]',
    style: {
      'shape': 'round-rectangle',
      'background-color': '#ffffff',
      'border-color': '#cbd5e1',
      'border-width': 1,
      'label': 'data(label)',
      'font-size': 10,
      'color': '#1e293b',
      'text-valign': 'center',
      'text-halign': 'center',
      'text-wrap': 'wrap',
      'text-max-width': 180,
      'width': 180,
      'height': 36,
      'padding': '4px',
    },
  },
  {
    selector: 'node[role = "target"]',
    style: { 'background-color': '#dcfce7', 'border-color': '#86efac' },
  },
  {
    selector: 'node[role = "dependency"]',
    style: { 'background-color': '#fffbeb', 'border-color': '#facc15', 'border-style': 'dashed' },
  },
  {
    selector: 'node[?matched]',
    style: { 'border-color': '#ef4444', 'border-width': 3 },
  },
  {
    selector: 'edge',
    style: {
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'line-color': '#2563eb',
      'target-arrow-color': '#2563eb',
      'width': (e) => Math.min(1 + Math.log2(e.data('count') || 1), 4),
      'label': (e) => ((e.data('count') || 1) > 1 ? `×${e.data('count')}` : ''),
      'font-size': 9,
      'color': '#475569',
      'text-background-color': '#ffffff',
      'text-background-opacity': 0.85,
      'text-background-padding': '2px',
    },
  },
  {
    selector: 'edge[?dependency]',
    style: {
      'line-color': '#d97706',
      'target-arrow-color': '#d97706',
      'line-style': 'dashed',
    },
  },
  // 路径模式：路径上 nodes/edges 高亮，其它半透明
  { selector: '.path-dim',       style: { 'opacity': 0.18 } },
  { selector: '.path-highlight', style: { 'border-color': '#7c3aed', 'border-width': 3 } },
  { selector: 'edge.path-highlight', style: { 'line-color': '#7c3aed', 'target-arrow-color': '#7c3aed', 'width': 3 } },
  { selector: '.path-endpoint',  style: { 'border-color': '#ef4444', 'border-width': 4 } },
]

const layoutConfig = computed(() => ({
  name: 'dagre',
  rankDir: layoutDir.value,
  nodeSep: SPACING_PRESETS[spacingPreset.value].nodeSep,
  rankSep: SPACING_PRESETS[spacingPreset.value].rankSep,
  fit: true,
  padding: 30,
  animate: false,
}))

const renderCy = async () => {
  await nextTick()
  if (!cyEl.value) return
  if (cy) {
    cy.destroy()
    cy = null
  }
  // 表视图模式不需要 cytoscape graph 实例
  if (viewMode.value !== 'graph') return
  if (!cyElements.value.length) return
  cy = cytoscape({
    container: cyEl.value,
    elements: cyElements.value,
    style: cyStyle,
    layout: layoutConfig.value,
    minZoom: 0.2,
    maxZoom: 2.5,
    wheelSensitivity: 0.2,
  })
  cy.on('tap', 'node[!isParent]', (evt) => {
    const id = evt.target.id()
    if (pathMode.value) {
      // 路径模式：第一次点选 from，第二次点选 to，再点重置
      if (!pathFrom.value) {
        pathFrom.value = id
      } else if (!pathTo.value && id !== pathFrom.value) {
        pathTo.value = id
        applyPathHighlight()
      } else {
        resetPathHighlight()
        pathFrom.value = id
      }
      return
    }
    selectedItem.value = { type: 'node', id }
    clickedFocalId.value = id
  })
  cy.on('tap', 'edge', (evt) => {
    if (pathMode.value) return
    const id = evt.target.id()
    selectedItem.value = { type: 'edge', id }
  })
}

// 路径高亮：从 cyAdjacency 里 BFS 算最短路径，避开 cytoscape elements
// 因为 compound parent 节点会干扰内置 BFS 的"邻居"语义。算出节点链后
// 用 cy.$id 拿元素再 addClass。
function shortestPath(from, to) {
  const { out } = cyAdjacency.value
  if (from === to) return [from]
  const prev = new Map()
  const visited = new Set([from])
  const queue = [from]
  while (queue.length) {
    const cur = queue.shift()
    if (cur === to) {
      const chain = [to]
      let walker = to
      while (prev.has(walker)) {
        walker = prev.get(walker)
        chain.unshift(walker)
      }
      return chain
    }
    for (const nb of out.get(cur) || []) {
      if (visited.has(nb)) continue
      visited.add(nb)
      prev.set(nb, cur)
      queue.push(nb)
    }
  }
  return null
}

function applyPathHighlight() {
  if (!cy || !pathFrom.value || !pathTo.value) return
  const chain = shortestPath(pathFrom.value, pathTo.value)
  cy.elements().removeClass('path-dim path-highlight path-endpoint')
  if (!chain) {
    // 没有路径：from 标 endpoint 红框，其它什么都不做
    const fromEl = cy.$id(pathFrom.value)
    if (fromEl.nonempty()) fromEl.addClass('path-endpoint')
    return
  }
  cy.elements().addClass('path-dim')
  for (let i = 0; i < chain.length; i++) {
    const node = cy.$id(chain[i])
    if (node.nonempty()) node.removeClass('path-dim').addClass('path-highlight')
    if (i + 1 < chain.length) {
      const edge = cy.edges(`[source = "${chain[i]}"][target = "${chain[i + 1]}"]`)
      if (edge.nonempty()) edge.removeClass('path-dim').addClass('path-highlight')
    }
  }
  cy.$id(pathFrom.value).addClass('path-endpoint')
  cy.$id(pathTo.value).addClass('path-endpoint')
}

function resetPathHighlight() {
  pathFrom.value = ''
  pathTo.value = ''
  if (cy) cy.elements().removeClass('path-dim path-highlight path-endpoint')
}

function togglePathMode() {
  pathMode.value = !pathMode.value
  if (!pathMode.value) resetPathHighlight()
}

const pathChainNames = computed(() => {
  if (!pathFrom.value || !pathTo.value) return null
  return shortestPath(pathFrom.value, pathTo.value)
})

const exportPng = () => {
  if (!cy) return
  const png = cy.png({ output: 'blob', full: true, scale: 2, bg: '#ffffff' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(png)
  link.download = 'lineage-graph-cy.png'
  link.click()
  URL.revokeObjectURL(link.href)
}

const exportJson = () => {
  const blob = new Blob([JSON.stringify(cyData.value, null, 2)], { type: 'application/json' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = 'lineage-graph-cy.json'
  link.click()
  URL.revokeObjectURL(link.href)
}

watch([layoutDir, spacingPreset, compoundBySchema], ([dir, preset, compound]) => {
  try {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ ...loadPrefs(), layoutDir: dir, spacingPreset: preset, compoundBySchema: compound }),
    )
  } catch {}
})

watch(cyElements, renderCy, { deep: true })
watch(layoutConfig, () => {
  if (cy) cy.layout(layoutConfig.value).run()
})

onMounted(renderCy)
onBeforeUnmount(() => cy?.destroy())
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
    <div class="grid gap-3 border-b border-slate-200 bg-white p-3">
      <div class="flex flex-wrap items-center gap-2">
        <input v-model="searchText" class="min-w-[220px] flex-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm" placeholder="搜索表名">
        <button class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50" @click="nextMatch">
          下一个 {{ searchMatches.length ? `${(matchIndex % searchMatches.length) + 1}/${searchMatches.length}` : '' }}
        </button>
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600" title="视图模式">
          <button class="rounded-md px-3 py-1.5" :class="viewMode === 'graph' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="viewMode = 'graph'">图</button>
          <button class="rounded-md px-3 py-1.5" :class="viewMode === 'table' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="viewMode = 'table'">表</button>
        </div>
        <button
          v-if="viewMode === 'graph'"
          type="button"
          class="rounded-lg border px-3 py-2 text-sm font-bold transition"
          :class="pathMode
            ? 'border-violet-300 bg-violet-50 text-violet-700'
            : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'"
          :title="pathMode ? '路径模式：依次选 from / to 节点' : '开启路径高亮模式'"
          @click="togglePathMode"
        >
          路径高亮 {{ pathMode ? 'ON' : 'OFF' }}
        </button>
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600">
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'neighborhood' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="focusMode = 'neighborhood'">周边</button>
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'upstream' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="focusMode = 'upstream'">上游</button>
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'downstream' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="focusMode = 'downstream'">下游</button>
          <button class="rounded-md px-3 py-1.5" :class="focusMode === 'all' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="focusMode = 'all'">全图</button>
        </div>
        <label class="flex items-center gap-2 text-sm text-slate-600" :class="focusMode === 'all' ? 'opacity-50' : ''">
          <span class="font-bold">跳数</span>
          <input v-model.number="hopDepth" type="range" min="1" max="5" step="1" :disabled="focusMode === 'all'" class="w-24">
          <span class="w-4 font-mono">{{ hopDepth }}</span>
        </label>
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600" title="布局方向">
          <button class="rounded-md px-3 py-1.5" :class="layoutDir === 'LR' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="layoutDir = 'LR'">横向</button>
          <button class="rounded-md px-3 py-1.5" :class="layoutDir === 'TB' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="layoutDir = 'TB'">纵向</button>
        </div>
        <div class="flex rounded-lg bg-slate-100 p-1 text-sm font-semibold text-slate-600" title="节点间距">
          <button class="rounded-md px-3 py-1.5" :class="spacingPreset === 'compact' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="spacingPreset = 'compact'">紧凑</button>
          <button class="rounded-md px-3 py-1.5" :class="spacingPreset === 'normal' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="spacingPreset = 'normal'">标准</button>
          <button class="rounded-md px-3 py-1.5" :class="spacingPreset === 'relaxed' ? 'bg-white text-violet-600 shadow-sm' : ''" @click="spacingPreset = 'relaxed'">宽松</button>
        </div>
        <label class="flex items-center gap-1 text-sm text-slate-600" title="把同一 schema 的表显示在 compound 容器内 —— Cytoscape 独有">
          <input v-model="compoundBySchema" type="checkbox" class="h-3.5 w-3.5">
          <span class="font-medium">Schema 容器</span>
        </label>
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
      <div v-if="scripts.length" class="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-xs text-slate-600">
        <p class="muted text-[11px]">
          Cytoscape 引擎用 compound 容器表达 schema，不需要"按 schema 折叠"按钮组。脚本过滤仍可用：
        </p>
        <div class="relative">
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
            <ul class="space-y-0.5">
              <li v-for="f in filteredScriptList" :key="f">
                <label
                  class="flex cursor-pointer items-center justify-between gap-2 rounded px-2 py-1 hover:bg-slate-50"
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
                  <span class="muted text-[10px]">{{ scriptCounts.get(f) || 0 }} 边</span>
                </label>
              </li>
              <li v-if="!filteredScriptList.length" class="muted px-2 py-2 text-center text-[11px]">无匹配脚本</li>
            </ul>
          </div>
        </div>
      </div>
      <div v-if="effectiveFocalId && focusMode !== 'all'" class="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-violet-50 px-3 py-2 text-sm text-violet-800">
        <span>聚焦 <code class="rounded bg-white px-1.5 py-0.5 font-mono text-violet-700">{{ effectiveFocalId }}</code> · {{ focusMode === 'upstream' ? '上游' : focusMode === 'downstream' ? '下游' : '周边' }} {{ hopDepth }} 跳 · {{ cyData.nodes.length }} 节点</span>
        <div class="flex gap-2">
          <button v-if="hopDepth < 5" class="font-bold text-violet-900 hover:underline" @click="hopDepth = Math.min(5, hopDepth + 1)">扩到 {{ hopDepth + 1 }} 跳</button>
          <button class="font-bold text-violet-900 hover:underline" @click="focusMode = 'all'; clickedFocalId = ''">显示全图</button>
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
      <!-- 路径模式状态提示 -->
      <div v-if="pathMode" class="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-violet-50 px-3 py-2 text-sm text-violet-800">
        <span>
          <strong>路径模式</strong> ·
          <template v-if="!pathFrom">点选起点节点</template>
          <template v-else-if="!pathTo">起点：<code class="rounded bg-white px-1.5 py-0.5 font-mono">{{ pathFrom }}</code> · 再点选终点</template>
          <template v-else-if="pathChainNames">
            <code class="rounded bg-white px-1.5 py-0.5 font-mono">{{ pathFrom }}</code>
            →
            <code class="rounded bg-white px-1.5 py-0.5 font-mono">{{ pathTo }}</code>
            · 经过 {{ pathChainNames.length - 1 }} 跳
          </template>
          <template v-else>
            <code class="rounded bg-white px-1.5 py-0.5 font-mono">{{ pathFrom }}</code>
            到
            <code class="rounded bg-white px-1.5 py-0.5 font-mono">{{ pathTo }}</code>
            <span class="ml-1 text-rose-700">无可达路径</span>
          </template>
        </span>
        <button v-if="pathFrom" class="font-bold text-violet-900 hover:underline" @click="resetPathHighlight">重置选择</button>
      </div>
    </div>
    <div class="grid lg:grid-cols-[minmax(0,1fr)_340px]">
      <div>
        <!-- Graph 视图 -->
        <div v-if="viewMode === 'graph'">
          <div v-if="!cyElements.length" class="p-6 text-center text-sm text-slate-500">
            <p>当前过滤条件下没有可绘制的血缘边</p>
            <p class="mt-1 text-[12px] text-slate-400">放宽顶部的角色 / 边类型 / 可信度筛选，或清空搜索词后重试。</p>
          </div>
          <div ref="cyEl" class="h-[520px] w-full"></div>
        </div>
        <!-- 表视图（>100 节点逃生通道）—— 复用 composable 的 tableRows -->
        <div v-else class="max-h-[520px] overflow-auto">
          <table class="w-full text-sm">
            <thead class="sticky top-0 bg-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-600">
              <tr>
                <th class="px-3 py-2">表</th>
                <th class="px-3 py-2">Schema</th>
                <th class="px-3 py-2">方向</th>
                <th class="px-3 py-2">跳数</th>
                <th class="px-3 py-2 text-right">上游</th>
                <th class="px-3 py-2 text-right">下游</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in tableRows" :key="row.id"
                class="cursor-pointer border-b border-slate-100 hover:bg-violet-50/40"
                @click="clickedFocalId = row.id; selectedItem = { type: 'node', id: row.id }; viewMode = 'graph'"
              >
                <td class="px-3 py-2 font-mono text-slate-800">{{ row.id }}</td>
                <td class="px-3 py-2 text-slate-600">{{ row.schema }}</td>
                <td class="px-3 py-2">
                  <span class="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-bold"
                    :class="row.direction === '聚焦' ? 'bg-violet-100 text-violet-700'
                          : row.direction === '上游' ? 'bg-emerald-100 text-emerald-700'
                          : row.direction === '下游' ? 'bg-amber-100 text-amber-700'
                          : 'text-slate-600'">{{ row.direction }}</span>
                </td>
                <td class="px-3 py-2 text-slate-600">{{ row.hops }}</td>
                <td class="px-3 py-2 text-right text-slate-600">{{ row.upstream }}</td>
                <td class="px-3 py-2 text-right text-slate-600">{{ row.downstream }}</td>
              </tr>
              <tr v-if="!tableRows.length">
                <td colspan="6" class="p-6 text-center text-slate-500">
                  没有命中节点 — 调整左上角的过滤条件，或在顶部搜索框输入表名重试
                </td>
              </tr>
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
