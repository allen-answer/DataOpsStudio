<script setup>
import { defineAsyncComponent, inject, computed } from 'vue'
import { Sparkles } from 'lucide-vue-next'
import SchemaPanel from '../components/SchemaPanel.vue'
import LineageReportView from './LineageReportView.vue'

const SqlEditor = defineAsyncComponent(() => import('../components/SqlEditor.vue'))

const { lineage, analyzeLineage } = inject('app')

// 从分析结果取 LineageAnalysisReport（Phase 3 后端附加字段）
const report = computed(() => lineage.result?.report || null)
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
