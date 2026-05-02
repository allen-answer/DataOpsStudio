<script setup>
import { defineAsyncComponent, inject } from 'vue'
import SchemaPanel from '../components/SchemaPanel.vue'

const LineageGraph = defineAsyncComponent(() => import('../components/LineageGraph.vue'))

const {
  batch,
  batchActiveTab,
  batchTabs,
  batchSelectedFileNames,
  analyzeBatch,
} = inject('app')
</script>

<template>
  <section class="space-y-6">
    <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div class="mb-6 flex items-end justify-between"><div><h2 class="text-2xl font-bold text-slate-800">多脚本 ETL 流程分析</h2><p class="mt-1 text-sm text-slate-500">支持 .sql/.txt/.zip、Schema 元数据和导出</p></div><button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="analyzeBatch">分析脚本包</button></div>
      <SchemaPanel :target="batch" sql-tables-label="只拉脚本中出现的表">
        <template #prefix>
          <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">SQL 方言</span><select v-model="batch.dialect" class="border-none bg-slate-50"><option value="">自动</option><option>mysql</option><option>oracle</option><option>tsql</option><option>postgres</option></select></label>
          <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">脚本文件</span><input type="file" accept=".sql,.txt,.zip" multiple class="border-none bg-slate-50" @change="batch.files = Array.from($event.target.files)"><small class="mt-2 block text-xs text-slate-500">可一次选择多个 .sql/.txt 文件，或上传一个 .zip 脚本包。</small></label>
        </template>
      </SchemaPanel>
      <div v-if="batchSelectedFileNames.length" class="mt-4 flex flex-wrap gap-2">
        <span v-for="name in batchSelectedFileNames" :key="name" class="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-600">{{ name }}</span>
      </div>
    </div>
    <div v-if="batch.error" class="rounded-2xl border border-red-200 bg-red-50 p-5 text-red-700">{{ batch.error }}</div>
    <div v-if="batch.result" class="space-y-6">
      <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 class="text-xl font-bold text-slate-800">分析结果</h2>
            <p class="mt-1 text-sm text-slate-500">结果按模块分块展示，避免多脚本结果像流水账一样堆在一屏。</p>
          </div>
          <div class="flex gap-2"><a class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50" :href="`/results/${batch.exports.excel_filename}`">下载 Excel</a><a class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50" :href="`/results/${batch.exports.json_filename}`">下载 JSON</a></div>
        </div>
        <div class="mt-5 flex flex-wrap gap-2">
          <button v-for="tab in batchTabs" :key="tab.id" class="rounded-xl px-4 py-2 text-sm font-bold transition" :class="batchActiveTab === tab.id ? 'bg-blue-600 text-white shadow-lg shadow-blue-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'" @click="batchActiveTab = tab.id">{{ tab.label }}</button>
        </div>
      </div>

      <div v-if="batchActiveTab === 'overview'" class="grid grid-cols-4 gap-3 xl:grid-cols-8">
        <div v-for="(value, key) in batch.result.summary" :key="key" class="rounded-2xl border border-slate-200 bg-white p-4 text-center shadow-sm"><strong class="block text-2xl text-slate-800">{{ value }}</strong><span class="text-xs text-slate-500">{{ key }}</span></div>
      </div>

      <div v-if="batchActiveTab === 'graph'" class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 class="mb-2 text-xl font-bold text-slate-800">表级数据流图</h2>
          <p class="mb-4 text-sm text-slate-500">这里展示的是“读取表 → 写入表”的 ETL 表级流向，不是任务调度 DAG。可拖动画布、滚轮缩放。</p>
          <LineageGraph :groups="batch.result.table_groups" :edges="batch.result.table_edges" />
        </div>
        <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h3 class="mb-4 font-bold text-slate-800">图中节点说明</h3>
          <div class="space-y-3 text-sm text-slate-600">
            <p><span class="mr-2 inline-block h-3 w-3 rounded bg-white ring-1 ring-slate-300"></span>来源表：脚本读取的数据表。</p>
            <p><span class="mr-2 inline-block h-3 w-3 rounded bg-green-100 ring-1 ring-green-300"></span>目标表：INSERT/CREATE TABLE AS 等写入表。</p>
            <p><span class="mr-2 inline-block h-3 w-3 rounded bg-amber-50 ring-1 ring-amber-300"></span>条件依赖：只在 WHERE/IN/EXISTS 等条件中参与过滤。</p>
            <p class="rounded-xl bg-slate-50 p-3 text-xs text-slate-500">如果图看起来空，通常是脚本里没有识别到写入目标表，或解析失败；可以切到“脚本清单/风险提示”查看原因。</p>
          </div>
        </div>
      </div>

      <div v-if="batchActiveTab === 'files'" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 class="mb-4 text-xl font-bold text-slate-800">脚本清单</h2>
        <div class="overflow-auto"><table><thead><tr><th>文件</th><th>状态</th><th>语句数</th><th>读取表</th><th>写入表</th><th>变量</th><th>提示</th></tr></thead><tbody><tr v-for="item in batch.result.files" :key="item.file_name"><td><code>{{ item.file_name }}</code></td><td>{{ item.status }}<p v-if="item.error" class="mt-1 text-xs text-red-600">{{ item.error }}</p></td><td>{{ item.statement_count }}</td><td>{{ item.read_tables.join(', ') }}</td><td>{{ item.write_tables.join(', ') }}</td><td>{{ item.variables.join(', ') }}</td><td>{{ item.warnings.map(w => w.type).join(', ') }}</td></tr></tbody></table></div>
      </div>

      <div v-if="batchActiveTab === 'edges'" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 class="mb-4 text-xl font-bold text-slate-800">表级流转</h2>
        <div class="overflow-auto"><table><thead><tr><th>来源表</th><th>目标表</th><th>类型</th><th>脚本</th><th>语句</th></tr></thead><tbody><tr v-for="item in batch.result.table_edges" :key="item.file_name + item.source_table + item.target_table + item.statement_index"><td>{{ item.source_table }}</td><td>{{ item.target_table }}</td><td>{{ item.edge_type }}</td><td><code>{{ item.file_name }}</code></td><td>{{ item.statement_index }}</td></tr></tbody></table></div>
      </div>

      <div v-if="batchActiveTab === 'deps'" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 class="mb-4 text-xl font-bold text-slate-800">跨脚本依赖</h2>
        <div class="overflow-auto"><table><thead><tr><th>产出脚本</th><th>消费脚本</th><th>中间表</th></tr></thead><tbody><tr v-for="item in batch.result.script_edges" :key="item.producer_file + item.consumer_file + item.table"><td><code>{{ item.producer_file }}</code></td><td><code>{{ item.consumer_file }}</code></td><td>{{ item.table }}</td></tr><tr v-if="!batch.result.script_edges.length"><td colspan="3" class="text-slate-400">未识别到跨脚本依赖</td></tr></tbody></table></div>
      </div>

      <div v-if="batchActiveTab === 'dag'" class="grid gap-6 xl:grid-cols-2">
        <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 class="mb-4 text-xl font-bold text-slate-800">拓扑顺序</h2>
          <div v-if="batch.result.dag?.topological_order?.length" class="flex flex-wrap gap-2">
            <span v-for="(name, index) in batch.result.dag.topological_order" :key="name" class="rounded-lg bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-700">{{ index + 1 }}. {{ name }}</span>
          </div>
          <p v-else class="text-sm text-amber-700">存在依赖环或没有可排序的跨脚本依赖。</p>
        </div>
        <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 class="mb-4 text-xl font-bold text-slate-800">依赖环</h2>
          <div v-if="batch.result.dag?.cycles?.length" class="space-y-2">
            <p v-for="cycle in batch.result.dag.cycles" :key="cycle.join('>')" class="rounded-xl bg-red-50 p-3 text-sm font-semibold text-red-700">{{ cycle.join(' → ') }}</p>
          </div>
          <p v-else class="text-sm text-slate-500">未发现脚本依赖环。</p>
        </div>
        <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm xl:col-span-2">
          <h2 class="mb-4 text-xl font-bold text-slate-800">上下游脚本</h2>
          <div class="overflow-auto"><table><thead><tr><th>脚本</th><th>上游</th><th>下游</th></tr></thead><tbody><tr v-for="name in batch.result.dag?.nodes || []" :key="name"><td><code>{{ name }}</code></td><td>{{ (batch.result.dag.upstream[name] || []).join(', ') }}</td><td>{{ (batch.result.dag.downstream[name] || []).join(', ') }}</td></tr></tbody></table></div>
        </div>
        <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm xl:col-span-2">
          <h2 class="mb-4 text-xl font-bold text-slate-800">多写冲突</h2>
          <div class="overflow-auto"><table><thead><tr><th>目标表</th><th>等级</th><th>写入脚本</th><th>说明</th></tr></thead><tbody><tr v-for="item in batch.result.dag?.write_conflicts || []" :key="item.table"><td>{{ item.table }}</td><td>{{ item.severity }}</td><td>{{ item.writers.join(', ') }}</td><td>{{ item.message }}</td></tr><tr v-if="!batch.result.dag?.write_conflicts?.length"><td colspan="4" class="text-slate-400">未发现多脚本写同一目标表冲突</td></tr></tbody></table></div>
        </div>
      </div>

      <div v-if="batchActiveTab === 'warnings'" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 class="mb-4 text-xl font-bold text-slate-800">风险提示</h2>
        <div class="overflow-auto"><table><thead><tr><th>文件</th><th>类型</th><th>说明</th></tr></thead><tbody><tr v-for="item in batch.result.warnings" :key="item.file_name + item.message"><td><code>{{ item.file_name }}</code></td><td>{{ item.type }}</td><td>{{ item.message }}</td></tr><tr v-if="!batch.result.warnings.length"><td colspan="3" class="text-slate-400">没有风险提示 — 所有脚本都成功解析，且没有发现可疑模式</td></tr></tbody></table></div>
      </div>
    </div>
  </section>
</template>
