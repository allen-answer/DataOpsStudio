<script setup>
import { computed, ref, watch } from 'vue'
import { GitBranch, ArrowRight } from 'lucide-vue-next'
import LineageFilterBar from './LineageFilterBar.vue'

// 影响分析：每张源表的下游传递闭包。
// 后端给的 impact.downstream 是已 flatten 的闭包（无深度信息）；要支持
// "深度 1 层 / 2 层 / 全部" 过滤，前端基于 table_edges 自己 BFS 一遍。
// 没传 edges 时退回旧行为（用 impact.downstream），仅"下游数 ≥ N" 过滤。
const props = defineProps({
  impact: { type: Object, default: () => ({ downstream: {} }) },
  edges: { type: Array, default: () => [] },
  preset: { type: Object, default: null },
})

// ---------------- 邻接表 ----------------
const adjacency = computed(() => {
  const out = new Map()
  for (const e of props.edges || []) {
    const s = (e.source_table || e.source || '').toLowerCase()
    const t = (e.target_table || e.target || '').toLowerCase()
    if (!s || !t) continue
    if (!out.has(s)) out.set(s, [])
    if (!out.get(s).includes(t)) out.get(s).push(t)
  }
  return out
})

// 计算每个源的"按深度分层下游"。BFS 直到没新节点 / 截断 maxDepth=10
function bfsLayers(startKey, adj, maxDepth = 10) {
  const layers = []
  let frontier = [startKey]
  const seen = new Set([startKey])
  for (let d = 1; d <= maxDepth; d++) {
    const next = []
    for (const node of frontier) {
      for (const neighbor of adj.get(node) || []) {
        if (seen.has(neighbor)) continue
        seen.add(neighbor)
        next.push(neighbor)
      }
    }
    if (!next.length) break
    layers.push(next)
    frontier = next
  }
  return layers
}

// 显示名 map（保留原大小写）—— 后端 impact.downstream 用什么大小写就还原什么
const nameMap = computed(() => {
  const map = new Map()
  for (const e of props.edges || []) {
    const s = e.source_table || e.source
    const t = e.target_table || e.target
    if (s) map.set(s.toLowerCase(), s)
    if (t) map.set(t.toLowerCase(), t)
  }
  // impact 里的源 / 下游也算入
  const ds = props.impact?.downstream || {}
  for (const [src, list] of Object.entries(ds)) {
    map.set(src.toLowerCase(), src)
    for (const t of list || []) map.set(t.toLowerCase(), t)
  }
  return map
})

// 全集（按 source 分组），每组带 layers + 闭包
const allEntries = computed(() => {
  const ds = props.impact?.downstream || {}
  const sources = Object.keys(ds).filter(s => (ds[s] || []).length)
  const adj = adjacency.value
  return sources.map((src) => {
    const layers = bfsLayers(src.toLowerCase(), adj)
    const flat = layers.flat()
    return {
      source: src,
      downstream: ds[src],          // 旧 closure（保底）
      layers,                       // [[depth1...], [depth2...], ...]
      depth: layers.length,
      flatByLayer: flat.map(k => nameMap.value.get(k) || k),
    }
  }).sort((a, b) => (b.flatByLayer.length || b.downstream.length) - (a.flatByLayer.length || a.downstream.length))
})

// ---------------- 筛选 ----------------
const search = ref('')
const minDownstream = ref('all') // 下游数：≥ N
const maxDepth = ref('all')      // 深度：1 / 2 / all

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return allEntries.value.filter(e => {
    if (kw) {
      const hay = `${e.source} ${e.downstream.join(' ')}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (minDownstream.value !== 'all') {
      const min = parseInt(minDownstream.value, 10) || 0
      if (e.downstream.length < min) return false
    }
    return true
  }).map(e => {
    // 按 maxDepth 截断 layers 显示
    if (maxDepth.value === 'all') return e
    const cap = parseInt(maxDepth.value, 10) || 1
    const cappedLayers = e.layers.slice(0, cap)
    return {
      ...e,
      layers: cappedLayers,
      flatByLayer: cappedLayers.flat().map(k => nameMap.value.get(k) || k),
    }
  })
})

const isFilterActive = computed(
  () => !!search.value || minDownstream.value !== 'all' || maxDepth.value !== 'all'
)

function resetFilters() {
  search.value = ''
  minDownstream.value = 'all'
  maxDepth.value = 'all'
}

watch(
  () => props.preset,
  (val) => {
    if (!val) return
    if (val.search != null) search.value = val.search
    if (val.minDownstream != null) minDownstream.value = val.minDownstream
    if (val.maxDepth != null) maxDepth.value = val.maxDepth
  },
  { immediate: true },
)

const hasEdges = computed(() => (props.edges || []).length > 0)
</script>

<template>
  <section class="card space-y-3">
    <div class="flex items-center justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-800">影响分析</h3>
        <p class="muted text-xs">每张源表的传递闭包下游（任意一张源表改了，会波及哪些下游）</p>
      </div>
    </div>

    <LineageFilterBar
      v-if="allEntries.length"
      v-model:search="search"
      search-placeholder="源表 / 下游表搜索"
      :total="allEntries.length"
      :visible="filtered.length"
      :active="isFilterActive"
      @clear="resetFilters"
    >
      <template #filters>
        <select v-model="minDownstream" class="filter-select">
          <option value="all">全部源表</option>
          <option value="2">下游 ≥ 2</option>
          <option value="5">下游 ≥ 5</option>
          <option value="10">下游 ≥ 10</option>
        </select>
        <select v-if="hasEdges" v-model="maxDepth" class="filter-select" title="只显示 N 跳之内的下游">
          <option value="all">全部深度</option>
          <option value="1">仅直接下游 (1 层)</option>
          <option value="2">2 层</option>
          <option value="3">3 层</option>
        </select>
      </template>
    </LineageFilterBar>

    <div v-if="!allEntries.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <GitBranch class="mx-auto mb-2 h-8 w-8 text-slate-300" />
      <p class="text-sm">无可分析的下游链路</p>
      <p class="muted text-xs">需要 INSERT 显式列出列名（SELECT * 暂不参与列级 lineage）</p>
    </div>

    <div v-else-if="!filtered.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <p class="text-sm">没有命中的影响链路</p>
      <p class="muted text-xs">调整搜索词或筛选条件，或点击"清空筛选"恢复</p>
    </div>

    <ul v-else class="space-y-2">
      <li
        v-for="entry in filtered" :key="entry.source"
        class="rounded-lg border border-slate-200 bg-slate-50 p-3"
      >
        <div class="flex flex-wrap items-center gap-2">
          <span class="sql-font text-sm font-semibold text-slate-800">{{ entry.source }}</span>
          <ArrowRight class="h-3.5 w-3.5 text-slate-300" />
          <span class="status-badge status-info">
            {{ hasEdges ? entry.flatByLayer.length : entry.downstream.length }} 张下游
          </span>
          <span v-if="hasEdges && entry.depth" class="muted text-[10px]">
            · 最深 {{ entry.depth }} 层
          </span>
        </div>
        <!-- hasEdges：按 layers 分组显示；无 edges 时退回 flat 闭包 -->
        <div v-if="hasEdges" class="mt-2 space-y-1.5">
          <div v-for="(layer, depthIdx) in entry.layers" :key="depthIdx" class="flex flex-wrap items-start gap-1">
            <span class="rounded bg-primary-light px-1.5 py-0.5 text-[10px] font-bold text-primary">
              {{ depthIdx + 1 }} 层
            </span>
            <span
              v-for="key in layer" :key="key"
              class="rounded bg-white px-2 py-0.5 text-xs sql-font text-slate-700 shadow-sm"
            >{{ nameMap.get(key) || key }}</span>
          </div>
        </div>
        <div v-else class="mt-2 flex flex-wrap gap-1">
          <span
            v-for="t in entry.downstream" :key="t"
            class="rounded bg-white px-2 py-0.5 text-xs sql-font text-slate-700 shadow-sm"
          >{{ t }}</span>
        </div>
      </li>
    </ul>
  </section>
</template>
