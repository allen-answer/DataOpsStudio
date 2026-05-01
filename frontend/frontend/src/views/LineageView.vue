<script setup>
import { defineAsyncComponent, inject } from 'vue'
import SchemaPanel from '../components/SchemaPanel.vue'

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
      <div
        v-if="lineage.result.warnings?.length || lineage.result.dynamic_sql_segments?.length || lineage.result.parse_errors?.length"
        class="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900 shadow-sm"
      >
        <h2 class="mb-4 text-xl font-bold text-amber-950">解析提示</h2>
        <div class="grid gap-4 xl:grid-cols-3">
          <div v-if="lineage.result.warnings?.length">
            <h3 class="mb-2 font-bold">风险提示</h3>
            <ul class="space-y-2">
              <li v-for="item in lineage.result.warnings" :key="item.type + item.message + item.statement_index" class="rounded-xl bg-white/70 p-3">
                <strong>{{ item.type }}</strong>
                <span v-if="item.statement_index" class="ml-2 text-xs text-amber-700">语句 {{ item.statement_index }}</span>
                <p class="mt-1 text-amber-800">{{ item.message }}</p>
              </li>
            </ul>
          </div>
          <div v-if="lineage.result.dynamic_sql_segments?.length">
            <h3 class="mb-2 font-bold">动态 SQL</h3>
            <div v-for="item in lineage.result.dynamic_sql_segments" :key="item.sql" class="mb-2 rounded-xl bg-white/70 p-3">
              <div class="mb-1 text-xs font-bold text-amber-700">{{ item.source }} · {{ item.confidence }}</div>
              <code class="break-all text-xs">{{ item.sql }}</code>
            </div>
          </div>
          <div v-if="lineage.result.parse_errors?.length">
            <h3 class="mb-2 font-bold">解析失败片段</h3>
            <div v-for="item in lineage.result.parse_errors" :key="item.sql + item.error" class="mb-2 rounded-xl bg-white/70 p-3">
              <p class="text-amber-800">{{ item.error }}</p>
              <code class="mt-2 block break-all text-xs">{{ item.sql }}</code>
            </div>
          </div>
        </div>
      </div>
      <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"><h2 class="mb-4 text-xl font-bold text-slate-800">表级拓扑图</h2><LineageGraph :groups="lineage.result.graph_groups" :edges="lineage.result.graph_edges" /></div>
      <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"><h2 class="mb-4 text-xl font-bold text-slate-800">字段血缘</h2><div class="overflow-auto"><table><thead><tr><th>输出字段</th><th>来源字段</th><th>来源表</th><th>可信度</th><th>变量</th><th>表达式</th></tr></thead><tbody><tr v-for="item in lineage.result.columns" :key="item.output_column + item.expression"><td>{{ item.output_column }}</td><td>{{ item.source_columns.join(', ') }}</td><td>{{ item.source_tables.join(', ') }}</td><td>{{ item.confidence || 'high' }}</td><td>{{ item.variables.join(', ') }}</td><td><code>{{ item.expression }}</code><p v-if="item.warnings?.length" class="mt-1 text-xs text-amber-600">{{ item.warnings.map(w => w.type).join(', ') }}</p></td></tr></tbody></table></div></div>
      <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"><h2 class="mb-4 text-xl font-bold text-slate-800">落表字段映射</h2><div class="overflow-auto"><table><thead><tr><th>目标表</th><th>目标字段</th><th>来源字段</th><th>来源表</th><th>可信度</th><th>处理逻辑</th></tr></thead><tbody><tr v-for="item in lineage.result.insert_mappings" :key="item.target_table + item.target_column + item.position"><td>{{ item.target_table }}</td><td>{{ item.target_column }}</td><td>{{ item.source_columns.join(', ') }}</td><td>{{ item.source_tables.join(', ') }}</td><td>{{ item.confidence || 'high' }}</td><td>{{ item.transform }}<p v-if="item.warnings?.length" class="mt-1 text-xs text-amber-600">{{ item.warnings.map(w => w.type).join(', ') }}</p></td></tr></tbody></table></div></div>
    </div>
  </section>
</template>
