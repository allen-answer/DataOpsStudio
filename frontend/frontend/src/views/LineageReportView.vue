<script setup>
import { ref, computed } from 'vue'
import {
  LayoutDashboard, Inbox, Upload, Workflow,
  Network, Layers, Sparkles, GitBranch, AlertTriangle, Bot,
} from 'lucide-vue-next'
import LineageSummaryPanel from '../components/lineage/LineageSummaryPanel.vue'
import LineageAssetPanel from '../components/lineage/LineageAssetPanel.vue'
import LineageStepsPanel from '../components/lineage/LineageStepsPanel.vue'
import LineageGraphPanel from '../components/lineage/LineageGraphPanel.vue'
import LineageSemanticPanel from '../components/lineage/LineageSemanticPanel.vue'
import LineageImpactPanel from '../components/lineage/LineageImpactPanel.vue'
import LineageRiskPanel from '../components/lineage/LineageRiskPanel.vue'
import LineageAIEnrichmentPanel from '../components/lineage/LineageAIEnrichmentPanel.vue'
import ColumnLineageGraph from '../components/lineage/ColumnLineageGraph.vue'
import LineageMappings from '../components/LineageMappings.vue'

// 9-tab 统一血缘报告视图。
// 单脚本 / 多脚本都接它：
// - report —— LineageAnalysisReport（result.report）
// - graphGroups / graphEdges —— 给 LineageGraphPanel 转发到 G6
// - columns / insertMappings —— 给 LineageMappings 字段血缘表
const props = defineProps({
  report: { type: Object, required: true },
  graphGroups: { type: Array, default: () => [] },
  graphEdges:  { type: Array, default: () => [] },
  columns: { type: Array, default: () => [] },
  insertMappings: { type: Array, default: () => [] },
  aiEnrichment: { type: Object, default: () => ({}) },
})

const TABS = [
  { id: 'summary', label: '总览',     icon: LayoutDashboard },
  { id: 'inputs',  label: '输入资产', icon: Inbox },
  { id: 'outputs', label: '输出资产', icon: Upload },
  { id: 'process', label: '处理过程', icon: Workflow },
  { id: 'table',   label: '表级血缘', icon: Network },
  { id: 'column',  label: '字段血缘', icon: Layers },
  { id: 'semantic',label: '语义血缘', icon: Sparkles },
  { id: 'impact',  label: '影响分析', icon: GitBranch },
  { id: 'risks',   label: '风险',     icon: AlertTriangle },
  { id: 'ai',      label: 'AI 辅助',  icon: Bot },
]

const activeTab = ref('summary')
const columnView = ref('table')
const focusedColumnNodeId = ref('')

// 总览卡片"点风险点 5"会 emit('navigate', {tab:'risks', preset:{levelFilter:'high'}})
// —— 这里把 tab 切过去，preset 透给目标 panel（一次性，view 后续 watch tab 会清掉）
const tabPresets = ref({})

function navigateTo(payload) {
  if (!payload || !payload.tab) return
  activeTab.value = payload.tab
  if (payload.preset) {
    tabPresets.value = { ...tabPresets.value, [payload.tab]: payload.preset }
  }
}

function focusColumn(payload) {
  focusedColumnNodeId.value = payload?.nodeId || ''
  columnView.value = 'graph'
  activeTab.value = 'column'
}

// 用户手动切 tab 时清掉对应 preset，避免预设干扰用户重新筛选
function onManualTabSwitch(tab) {
  activeTab.value = tab
  if (tabPresets.value[tab]) {
    const next = { ...tabPresets.value }
    delete next[tab]
    tabPresets.value = next
  }
}

const tabBadgeCount = computed(() => ({
  inputs:  props.report.inputs?.length || 0,
  outputs: props.report.outputs?.length || 0,
  process: props.report.process_steps?.length || 0,
  table:   props.report.table_edges?.length || 0,
  column:  props.report.column_edges?.length || 0,
  impact:  Object.keys(props.report.impact_analysis?.downstream || {}).length,
  risks:   props.report.risks?.length || 0,
  ai:      props.aiEnrichment?.enabled ? 1 : 0,
}))
</script>

<template>
  <div class="space-y-4">
    <!-- Tab bar -->
    <div class="flex flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-soft">
      <button
        v-for="tab in TABS" :key="tab.id"
        type="button"
        class="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition"
        :class="activeTab === tab.id
          ? 'bg-primary text-white shadow-sm'
          : 'text-slate-600 hover:bg-slate-100'"
        @click="onManualTabSwitch(tab.id)"
      >
        <component :is="tab.icon" class="h-3.5 w-3.5" />
        <span>{{ tab.label }}</span>
        <span
          v-if="tabBadgeCount[tab.id]"
          class="rounded-full px-1.5 py-0.5 text-[10px] font-bold"
          :class="activeTab === tab.id ? 'bg-white/30' : 'bg-slate-100 text-slate-600'"
        >{{ tabBadgeCount[tab.id] }}</span>
      </button>
    </div>

    <!-- Tab panels -->
    <div v-if="activeTab === 'summary'">
      <LineageSummaryPanel :report="report" @navigate="navigateTo" />
    </div>

    <LineageAssetPanel
      v-else-if="activeTab === 'inputs'"
      kind="inputs"
      :assets="report.inputs"
      :preset="tabPresets.inputs"
    />

    <LineageAssetPanel
      v-else-if="activeTab === 'outputs'"
      kind="outputs"
      :assets="report.outputs"
      :preset="tabPresets.outputs"
    />

    <LineageStepsPanel
      v-else-if="activeTab === 'process'"
      :steps="report.process_steps"
      :preset="tabPresets.process"
    />

    <div v-else-if="activeTab === 'table'">
      <LineageGraphPanel
        v-if="graphEdges.length || graphGroups.length"
        :groups="graphGroups"
        :edges="graphEdges"
      />
      <div v-else class="card border-dashed py-10 text-center text-slate-400">
        <Network class="mx-auto mb-2 h-8 w-8 text-slate-300" />
        <p class="text-sm">无表级图谱数据</p>
        <p class="muted text-xs">SELECT * 不会产生图边；用具名列重试</p>
      </div>
    </div>

    <section v-else-if="activeTab === 'column'" class="card">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-base font-semibold text-slate-800">字段血缘</h3>
        <div class="rounded-lg border border-slate-200 bg-slate-50 p-1">
          <button
            class="rounded-md px-3 py-1.5 text-xs font-semibold transition"
            :class="columnView === 'table' ? 'bg-white text-primary shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="columnView = 'table'"
          >表格</button>
          <button
            class="rounded-md px-3 py-1.5 text-xs font-semibold transition"
            :class="columnView === 'graph' ? 'bg-white text-primary shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="columnView = 'graph'"
          >字段图</button>
        </div>
      </div>
      <LineageMappings
        v-if="columnView === 'table'"
        :columns="columns"
        :insert-mappings="insertMappings"
        :preset="tabPresets.column"
        @focus-column="focusColumn"
      />
      <ColumnLineageGraph
        v-else
        :edges="report.column_edges"
        :impact="report.column_impact_analysis"
        :focus-node-id="focusedColumnNodeId"
      />
    </section>

    <LineageSemanticPanel
      v-else-if="activeTab === 'semantic' && report.semantic_lineage"
      :semantic="report.semantic_lineage"
    />
    <div v-else-if="activeTab === 'semantic'" class="card border-dashed py-10 text-center text-slate-400">
      <Sparkles class="mx-auto mb-2 h-8 w-8 text-slate-300" />
      <p class="text-sm">无语义血缘数据（多脚本暂未做语义聚合）</p>
    </div>

    <LineageImpactPanel
      v-else-if="activeTab === 'impact'"
      :impact="report.impact_analysis"
      :edges="report.table_edges"
      :preset="tabPresets.impact"
    />

    <LineageRiskPanel
      v-else-if="activeTab === 'risks'"
      :risks="report.risks"
      :preset="tabPresets.risks"
    />

    <LineageAIEnrichmentPanel
      v-else-if="activeTab === 'ai'"
      :enrichment="aiEnrichment"
    />
  </div>
</template>
