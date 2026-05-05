<script setup>
import { defineAsyncComponent, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Sparkles } from 'lucide-vue-next'
import SchemaPanel from '../components/SchemaPanel.vue'
import LineageReportView from './LineageReportView.vue'
import { apiGet } from '../api'
import { useLineageStore } from '../stores/lineage'

const SqlEditor = defineAsyncComponent(() => import('../components/SqlEditor.vue'))

const lineageStore = useLineageStore()
const { lineage } = lineageStore       // reactive 直接拿
const { analyzeLineage } = lineageStore
const route = useRoute()

// 从分析结果取 LineageAnalysisReport（Phase 3 后端附加字段）
const report = computed(() => lineage.result?.report || null)
const isStressMode = computed(() => !!lineage.result?.stress_fixture)

// Phase 10 #1：URL ?stress=N → 加载合成压测 fixture，跳过分析。
// 用于在浏览器跑 G6 / Cytoscape 双引擎压测对比。
onMounted(async () => {
  const stress = route.query.stress
  if (!stress) return
  const size = parseInt(stress, 10)
  if (Number.isNaN(size) || size < 10 || size > 10000) {
    lineage.error = `stress 参数必须在 [10, 10000] 区间，当前 ${stress}`
    return
  }
  try {
    lineage.error = ''
    const data = await apiGet(`/api/lineage/stress-fixture?size=${size}`)
    lineage.result = data
  } catch (e) {
    lineage.error = `加载 stress fixture 失败：${e.message || e}`
  }
})
</script>

<template>
  <section class="space-y-4">
    <!-- 输入区：SQL 编辑器 + Schema -->
    <div class="card">
      <div class="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 class="text-2xl font-bold text-slate-800">单脚本血缘分析</h2>
          <p class="muted text-sm">Schema 联动、变量识别、字段级明细 + 9 维统一展示报告</p>
        </div>
        <button class="btn btn-primary" @click="analyzeLineage">
          <Sparkles class="h-4 w-4" /> 分析血缘
        </button>
      </div>
      <SqlEditor v-model="lineage.sql" placeholder="粘贴 SQL，或选择文件" />
      <div class="mt-4">
        <SchemaPanel :target="lineage" sql-tables-label="只拉 SQL 中出现的表">
          <template #prefix>
            <label>
              <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">SQL 方言</span>
              <select v-model="lineage.dialect" class="bg-slate-50">
                <option value="">自动</option>
                <option>mysql</option>
                <option>oracle</option>
                <option>tsql</option>
                <option>postgres</option>
              </select>
            </label>
            <label>
              <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">SQL/TXT 文件</span>
              <input
                type="file" accept=".sql,.txt" class="bg-slate-50"
                @change="lineage.sqlFile = $event.target.files[0]"
              >
            </label>
          </template>
        </SchemaPanel>
      </div>
    </div>

    <!-- Phase 10 #1：压测模式提示 -->
    <div v-if="isStressMode" class="card border-purple-200 bg-purple-50/40 p-3 text-sm">
      <div class="flex items-start gap-2">
        <Sparkles class="mt-0.5 h-4 w-4 shrink-0 text-purple-600" />
        <div>
          <p class="font-bold text-purple-800">压测 fixture 模式</p>
          <p class="muted text-[12px] leading-relaxed">
            正在显示 <strong>{{ lineage.result.stress_size }}</strong> 张合成表的血缘图。
            建议 Chrome DevTools Performance 录一段（init → 拖动 → 缩放 → focal 切换 → schema 折叠），
            分别在 G6 / Cytoscape 引擎下各做一次，对比 main thread 耗时 / FPS / Memory 峰值。
            URL 改 ?stress=300 / 1000 / 5000 切换 fixture 大小。
          </p>
        </div>
      </div>
    </div>

    <!-- 错误条 -->
    <div v-if="lineage.error" class="card border-status-error-bg bg-status-error-bg/40 text-status-error">
      {{ lineage.error }}
    </div>

    <!-- 9-tab 报告 -->
    <LineageReportView
      v-if="report"
      :report="report"
      :graph-groups="lineage.result.graph_groups || []"
      :graph-edges="lineage.result.graph_edges || []"
      :columns="lineage.result.columns || []"
      :insert-mappings="lineage.result.insert_mappings || []"
      :ai-enrichment="lineage.result.ai_enrichment || {}"
      :ai-inferred="lineage.result.ai_inferred || {}"
      :parse-errors="lineage.result.parse_errors || []"
      :dynamic-sql-segments="lineage.result.dynamic_sql_segments || []"
      :ambiguous-column-warnings="lineage.result.warnings || []"
    />
  </section>
</template>
