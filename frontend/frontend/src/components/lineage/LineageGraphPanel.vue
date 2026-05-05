<script setup>
// 图谱 wrapper —— 在 G6（稳定）和 Cytoscape（实验）两个引擎之间切换。
// 引擎选择持久化到 localStorage 的 lineage-graph-prefs-v1.engine。
// 两个组件都接受同一份 (groups, edges) prop，通过 useLineageGraphData
// 共享数据派生逻辑。
//
// Phase 10 #3 enhancement：节点叠 PII / SLA / owner 徽章 ——
// onMount 拉一次 /api/assets/aspects/index 拿表→aspects 映射，传给两引擎。
// 引擎用 emoji 前缀（🔒 PII / ⏰ SLA / 👤 owner / ⚠️ sensitive）添到 label，
// 不动节点几何。
import { defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { apiGet } from '../../api'

const LineageGraph = defineAsyncComponent(() => import('../LineageGraph.vue'))
const LineageGraphCytoscape = defineAsyncComponent(() => import('../LineageGraphCytoscape.vue'))

defineProps({
  groups: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
})

const PREFS_KEY = 'lineage-graph-prefs-v1'
const loadEngine = () => {
  try {
    const prefs = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}')
    return prefs.engine === 'cytoscape' ? 'cytoscape' : 'g6'
  } catch {
    return 'g6'
  }
}

const engine = ref(loadEngine())
const aspectsByTable = ref({})        // {tableName: [{aspect_type, value, ...}]}
const aspectsLoading = ref(false)

watch(engine, (val) => {
  try {
    const prefs = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}')
    localStorage.setItem(PREFS_KEY, JSON.stringify({ ...prefs, engine: val }))
  } catch {}
})

async function loadAspectsIndex() {
  aspectsLoading.value = true
  try {
    aspectsByTable.value = await apiGet(
      '/api/assets/aspects/index?types=pii,sla,owner,sensitive&asset_kind=table',
    ) || {}
  } catch {
    // 静默失败 —— aspects 拉不到不影响图渲染（只是没徽章）
    aspectsByTable.value = {}
  } finally {
    aspectsLoading.value = false
  }
}

onMounted(loadAspectsIndex)
</script>

<template>
  <section class="card overflow-hidden p-0">
    <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3">
      <div>
        <h3 class="text-base font-semibold text-slate-800">表级血缘图</h3>
        <p class="muted text-xs">
          节点 = 表，边 = 写入关系；hover 节点查看角色 / 业务分组
          <span v-if="engine === 'cytoscape'" class="ml-1 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-violet-700">实验</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <span class="muted text-[11px]">引擎</span>
        <div class="flex rounded-lg bg-white p-1 text-xs font-semibold text-slate-600 ring-1 ring-slate-200" title="G6 是稳定实现；Cytoscape 用 compound 容器表达 schema，正在验证">
          <button
            class="rounded-md px-2.5 py-1"
            :class="engine === 'g6' ? 'bg-primary text-white shadow-sm' : 'hover:bg-slate-100'"
            @click="engine = 'g6'"
          >G6</button>
          <button
            class="rounded-md px-2.5 py-1"
            :class="engine === 'cytoscape' ? 'bg-violet-600 text-white shadow-sm' : 'hover:bg-slate-100'"
            @click="engine = 'cytoscape'"
          >Cytoscape</button>
        </div>
      </div>
    </div>
    <!-- Aspect 徽章图例 —— 解释节点 label 前缀 emoji -->
    <div
      v-if="Object.keys(aspectsByTable).length"
      class="flex flex-wrap items-center gap-3 border-b border-slate-100 bg-amber-50/60 px-4 py-1.5 text-[11px] text-slate-600"
    >
      <span class="font-semibold text-slate-700">分类徽章</span>
      <span>🔒 PII</span>
      <span>⏰ SLA</span>
      <span>👤 owner</span>
      <span>⚠️ sensitive</span>
      <span class="muted ml-auto">
        覆盖 {{ Object.keys(aspectsByTable).length }} 张表 ·
        <a href="#/admin/governance" class="text-primary hover:underline">分类治理</a>
      </span>
    </div>
    <div class="p-2">
      <LineageGraph v-if="engine === 'g6'" :groups="groups" :edges="edges" :aspects-by-table="aspectsByTable" />
      <LineageGraphCytoscape v-else :groups="groups" :edges="edges" :aspects-by-table="aspectsByTable" />
    </div>
  </section>
</template>
