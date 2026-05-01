<script setup>
import { defineAsyncComponent, inject } from 'vue'
import LineageMappings from '../components/LineageMappings.vue'
import SchemaPanel from '../components/SchemaPanel.vue'
import WarningsPanel from '../components/WarningsPanel.vue'

const SqlEditor = defineAsyncComponent(() => import('../components/SqlEditor.vue'))
const LineageGraph = defineAsyncComponent(() => import('../components/LineageGraph.vue'))

const { lineage, analyzeLineage } = inject('app')
</script>

<template>
  <section class="space-y-6">
    <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div class="mb-6 flex items-end justify-between">
        <div><h2 class="text-2xl font-bold text-slate-800">SQL 血缘分析</h2><p class="mt-1 text-sm text-slate-500">Schema 联动、变量识别、字段级明细和 G6 拓扑图</p></div>
        <button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="analyzeLineage">分析血缘</button>
      </div>
      <SqlEditor v-model="lineage.sql" placeholder="粘贴 SQL，或选择文件" />
      <div class="mt-4">
        <SchemaPanel :target="lineage" sql-tables-label="只拉 SQL 中出现的表">
          <template #prefix>
            <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">SQL 方言</span><select v-model="lineage.dialect" class="border-none bg-slate-50"><option value="">自动</option><option>mysql</option><option>oracle</option><option>tsql</option><option>postgres</option></select></label>
            <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">SQL/TXT 文件</span><input type="file" accept=".sql,.txt" class="border-none bg-slate-50" @change="lineage.sqlFile = $event.target.files[0]"></label>
          </template>
        </SchemaPanel>
      </div>
    </div>
    <div v-if="lineage.error" class="rounded-2xl border border-red-200 bg-red-50 p-5 text-red-700">{{ lineage.error }}</div>
    <div v-if="lineage.result" class="grid gap-6">
      <WarningsPanel
        :warnings="lineage.result.warnings || []"
        :dynamic-sql-segments="lineage.result.dynamic_sql_segments || []"
        :parse-errors="lineage.result.parse_errors || []"
      />
      <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 class="mb-4 text-xl font-bold text-slate-800">表级拓扑图</h2>
        <LineageGraph :groups="lineage.result.graph_groups" :edges="lineage.result.graph_edges" />
      </div>
      <LineageMappings :columns="lineage.result.columns || []" :insert-mappings="lineage.result.insert_mappings || []" />
    </div>
  </section>
</template>
