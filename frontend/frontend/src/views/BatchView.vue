<script setup>
import { inject, computed } from 'vue'
import { Sparkles } from 'lucide-vue-next'
import SchemaPanel from '../components/SchemaPanel.vue'
import LineageReportView from './LineageReportView.vue'

const {
  batch,
  batchSelectedFileNames,
  analyzeBatch,
} = inject('app')

const report = computed(() => batch.result?.report || null)
</script>

<template>
  <section class="space-y-4">
    <div class="card">
      <div class="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 class="text-2xl font-bold text-slate-800">多脚本 ETL 流程分析</h2>
          <p class="muted text-sm">支持 .sql/.txt/.zip、Schema 元数据和导出；与单脚本同 9 维报告</p>
        </div>
        <button class="btn btn-primary" @click="analyzeBatch">
          <Sparkles class="h-4 w-4" /> 分析脚本包
        </button>
      </div>
      <SchemaPanel :target="batch" sql-tables-label="只拉脚本中出现的表">
        <template #prefix>
          <label>
            <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">SQL 方言</span>
            <select v-model="batch.dialect" class="bg-slate-50">
              <option value="">自动</option>
              <option>mysql</option>
              <option>oracle</option>
              <option>tsql</option>
              <option>postgres</option>
            </select>
          </label>
          <label>
            <span class="muted mb-1 block text-[10px] font-bold uppercase tracking-wider">脚本文件</span>
            <input
              type="file" accept=".sql,.txt,.zip" multiple class="bg-slate-50"
              @change="batch.files = Array.from($event.target.files)"
            >
            <small class="muted mt-1 block text-[11px]">可一次选择多个 .sql/.txt 文件，或上传一个 .zip 脚本包</small>
          </label>
        </template>
      </SchemaPanel>
      <div v-if="batchSelectedFileNames.length" class="mt-3 flex flex-wrap gap-1.5">
        <span v-for="name in batchSelectedFileNames" :key="name" class="rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary sql-font">{{ name }}</span>
      </div>
    </div>

    <!-- 错误条 -->
    <div v-if="batch.error" class="card border-status-error-bg bg-status-error-bg/40 text-status-error">
      {{ batch.error }}
    </div>

    <!-- 导出条（多脚本独有） -->
    <div v-if="batch.exports?.excel_filename" class="card flex flex-wrap items-center justify-between gap-3">
      <span class="muted text-xs">分析完成，可下载完整报告：</span>
      <div class="flex gap-2">
        <a class="btn btn-outline h-9 gap-1.5 px-3 text-xs" :href="`/results/${batch.exports.excel_filename}`">下载 Excel</a>
        <a class="btn btn-outline h-9 gap-1.5 px-3 text-xs" :href="`/results/${batch.exports.json_filename}`">下载 JSON</a>
      </div>
    </div>

    <!-- 9-tab 报告 -->
    <LineageReportView
      v-if="report"
      :report="report"
      :graph-groups="batch.result.table_groups || []"
      :graph-edges="batch.result.table_edges || []"
      :columns="[]"
      :insert-mappings="batch.result.field_mappings || []"
      :ai-enrichment="batch.result.ai_enrichment || {}"
      :ai-inferred="batch.result.ai_inferred || {}"
      :parse-errors="batch.result.parse_errors || []"
      :dynamic-sql-segments="batch.result.dynamic_sql_segments || []"
    />
  </section>
</template>
