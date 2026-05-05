<script setup>
import { computed } from 'vue'
import { useBootstrapStore } from '../stores/bootstrap'

// `target` is the parent's reactive (lineage / batch).
// `sqlTablesLabel` differs between Lineage ("只拉 SQL 中出现的表") and Batch ("只拉脚本中出现的表").
const props = defineProps({
  target: { type: Object, required: true },
  sqlTablesLabel: { type: String, default: '只拉 SQL 中出现的表' },
})

const { state } = useBootstrapStore()

const schemaFileNames = computed(() => (props.target.schemaFiles || []).map((file) => file.name))
</script>

<template>
  <div class="grid grid-cols-4 gap-4">
    <slot name="prefix" />
    <label>
      <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 数据源</span>
      <select v-model="target.schemaDatasourceId" class="border-none bg-slate-50">
        <option value="">不自动拉取</option>
        <option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option>
      </select>
      <small class="mt-2 block text-xs text-slate-500">失败时会降级为文件元数据。</small>
    </label>
    <label>
      <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 元数据</span>
      <input type="file" accept=".json,.sql,.txt,.zip" multiple class="border-none bg-slate-50" @change="target.schemaFiles = Array.from($event.target.files)">
      <small class="mt-2 block text-xs text-slate-500">文件会和数据源字段合并。</small>
    </label>
  </div>
  <div class="mt-4 grid grid-cols-4 gap-4">
    <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema / Database</span><input v-model="target.schemaName" class="border-none bg-slate-50" placeholder="留空使用数据源默认值"></label>
    <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">表名过滤</span><input v-model="target.schemaTableFilter" class="border-none bg-slate-50" placeholder="如 ods_%"></label>
    <label>
      <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 方言</span>
      <select v-model="target.schemaDialect" class="border-none bg-slate-50">
        <option value="">跟随数据源</option>
        <option value="mysql">MySQL</option>
        <option value="oracle">Oracle</option>
        <option value="dm">DM</option>
        <option value="ob_mysql">OB MySQL</option>
        <option value="ob_oracle">OB Oracle</option>
      </select>
    </label>
    <label class="flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-600">
      <input v-model="target.schemaOnlySqlTables" type="checkbox" class="h-4 w-4">{{ sqlTablesLabel }}
    </label>
  </div>
  <div v-if="schemaFileNames.length" class="mt-4 flex flex-wrap gap-2">
    <span v-for="name in schemaFileNames" :key="name" class="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-600">{{ name }}</span>
  </div>
</template>
