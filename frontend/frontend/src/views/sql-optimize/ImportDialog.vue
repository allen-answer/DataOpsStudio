<script setup lang="ts">
// 从 datasource 反向导入 yml 对话框(Phase 14 P1-1 UI + P2 拆出)
import { Database, AlertCircle, CheckCircle2 } from 'lucide-vue-next'
import { useSchemaImportStore } from '../../stores/schemaImport'

const store = useSchemaImportStore()
</script>

<template>
  <div v-if="store.importDialogOpen" class="card border-primary bg-primary-light/10 p-4 space-y-3">
    <div class="flex items-center justify-between">
      <h3 class="font-bold text-primary flex items-center gap-2">
        <Database class="h-5 w-5" />
        从 datasource 导入 schema → 生成 scenario yml
      </h3>
      <button class="text-slate-500 hover:text-slate-800" @click="store.importDialogOpen = false">✕</button>
    </div>
    <p class="text-xs text-slate-600">
      从真实 datasource 拉表的 columns / indexes / 行数,翻成 scenario yml(自动推断 generator + 加 PRIMARY KEY)。
      生成后填 TODO workload SQL 就能开始优化。
    </p>
    <div class="grid grid-cols-2 gap-3 text-sm">
      <label class="block">
        <span class="text-slate-700 text-xs font-semibold">Datasource</span>
        <select v-model="store.importForm.datasource_id" class="mt-1 w-full">
          <option value="">- 选 datasource -</option>
          <option v-for="d in store.mysqlDatasources" :key="(d as any).id" :value="(d as any).id">
            {{ (d as any).name }} ({{ (d as any).db_type }})
          </option>
        </select>
      </label>
      <label class="block">
        <span class="text-slate-700 text-xs font-semibold">Scenario ID(文件名)</span>
        <input v-model="store.importForm.scenario_id" class="mt-1 w-full" placeholder="orders-perf" />
      </label>
      <label class="block col-span-2">
        <span class="text-slate-700 text-xs font-semibold">Table 名(逗号分隔,支持 schema.table)</span>
        <input v-model="store.importForm.table_names" class="mt-1 w-full sql-font" placeholder="ods.orders, ods.users" />
      </label>
      <label class="block">
        <span class="text-slate-700 text-xs font-semibold">Scenario 名(可选)</span>
        <input v-model="store.importForm.scenario_name" class="mt-1 w-full" placeholder="Orders 性能场景" />
      </label>
      <label class="block">
        <span class="text-slate-700 text-xs font-semibold">缺统计时默认行数</span>
        <input v-model.number="store.importForm.default_rows" type="number" class="mt-1 w-full" min="1" max="1000000" />
      </label>
      <label class="col-span-2 flex items-center gap-2 text-xs text-slate-700">
        <input type="checkbox" v-model="store.importForm.save" />
        直接保存到 <code class="sql-font">config/scenarios/{{ store.importForm.scenario_id || '<id>' }}.yml</code>
      </label>
    </div>
    <div v-if="store.importError" class="text-status-error text-xs flex items-center gap-1">
      <AlertCircle class="h-3.5 w-3.5" /> {{ store.importError }}
    </div>
    <div class="flex items-center gap-2">
      <button class="btn btn-primary" :disabled="store.importing" @click="store.submitImport">
        {{ store.importing ? '导入中…' : '✨ 导入' }}
      </button>
      <button class="btn btn-outline" @click="store.importDialogOpen = false">取消</button>
    </div>
    <div v-if="store.importResult" class="border-t border-primary/20 pt-3 space-y-2">
      <div class="text-status-success font-bold text-xs flex items-center gap-1">
        <CheckCircle2 class="h-3.5 w-3.5" />
        导入成功:{{ store.importResult.tables_imported }} 张表
        {{ store.importResult.saved_path ? `· 已保存到 ${store.importResult.saved_path}` : '· 未保存(仅返 yml)' }}
      </div>
      <div class="flex gap-2">
        <button class="btn btn-outline h-7 px-2 text-[11px]" @click="store.copyImportYml">📋 复制 yml</button>
      </div>
      <pre class="rounded bg-slate-900 text-slate-100 p-3 text-[11px] sql-font max-h-72 overflow-auto">{{ store.importResult.yml_text }}</pre>
    </div>
  </div>
</template>
