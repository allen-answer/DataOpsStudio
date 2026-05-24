<script setup lang="ts">
/**
 * Phase 14 #3 — Schema 导入 view
 *
 * 从 datasource 反向读 information_schema / all_tab_columns → 生成 scenario yml。
 *
 * 风控:
 * - SCHEMA_IMPORT_PREVIEW:任何环境(prod 需 allow_schema_import)
 * - SCHEMA_IMPORT_SAVE:仅 sandbox + allow_schema_save
 *   生产环境 save=True 后端会 403,前端 disable Save 按钮 + 文案警告
 */
import { ref, computed } from 'vue'
import { Database, FileCode, Save, Eye, Copy, AlertTriangle } from 'lucide-vue-next'
import { useSandboxStore } from '../stores/sandbox'
import { apiJson } from '../api'
import { useNoticeStore } from '../stores/notice'
import OperationRiskPanel from '../components/sql/OperationRiskPanel.vue'

const store = useSandboxStore()
const noticeStore = useNoticeStore()

const datasourceId = ref('')
const scenarioId = ref('')
const scenarioName = ref('')
const tableNamesText = ref('')   // 逗号 / 换行分隔
const defaultRows = ref(1000)
const loading = ref(false)
const error = ref('')
const previewYml = ref('')
const savedPath = ref('')

const selectedDs = computed(() => {
  const id = datasourceId.value
  return (store.datasources as any[]).find((d: any) => d.id === id) || null
})

const parsedTableNames = computed(() =>
  tableNamesText.value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean),
)

// 当前 ds 是否允许 save(给 button disable + 文案用)
const canSave = computed(() => {
  const ds = selectedDs.value as any
  if (!ds) return false
  return ds.environment === 'sandbox' && !!ds.allow_schema_save
})

async function callImport(save: boolean) {
  error.value = ''
  if (!datasourceId.value) {
    error.value = '请选 datasource'
    return
  }
  if (!scenarioId.value.match(/^[A-Za-z0-9_\-]+$/)) {
    error.value = 'scenario id 只允许字母 / 数字 / _ / -'
    return
  }
  if (!parsedTableNames.value.length) {
    error.value = '至少填一张表名'
    return
  }
  loading.value = true
  try {
    const body = await apiJson<{
      scenario_id: string
      yml_text: string
      saved_path?: string | null
      tables_imported: number
    }>('/api/scenarios/import-from-datasource', 'POST', {
      datasource_id: datasourceId.value,
      table_names: parsedTableNames.value,
      scenario_id: scenarioId.value,
      scenario_name: scenarioName.value || scenarioId.value,
      default_rows: defaultRows.value,
      save,
    })
    previewYml.value = body.yml_text
    savedPath.value = body.saved_path || ''
    if (save && body.saved_path) {
      noticeStore.setNotice(`yml 已保存到 config/scenarios/${body.saved_path}`)
    }
  } catch (e: any) {
    error.value = noticeStore.toErrorMessage(e)
  } finally {
    loading.value = false
  }
}

async function copyYml() {
  if (!previewYml.value) return
  try {
    await navigator.clipboard.writeText(previewYml.value)
    noticeStore.setNotice('yml 已复制到剪贴板')
  } catch {
    noticeStore.setNotice('复制失败,请手动选中复制')
  }
}
</script>

<template>
  <section class="space-y-6">
    <div class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800 flex items-center gap-2">
          <FileCode class="h-7 w-7 text-primary" />
          Schema 导入
        </h2>
        <p class="mt-1 text-sm text-slate-500">
          从 datasource 读 information_schema / all_tab_columns,反向生成
          scenario yml(给 <a href="#/scenario-lab" class="text-primary hover:underline">场景测试沙盒</a>
          用)。
          <span class="text-status-error font-semibold">生产 ds 默认只能 preview,不能 save。</span>
        </p>
      </div>
    </div>

    <!-- 表单 -->
    <div class="card p-5 space-y-4">
      <div>
        <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
          <Database class="h-3 w-3 inline" /> 源 datasource
        </label>
        <select v-model="datasourceId" class="w-full">
          <option value="" disabled>—— 选一个 ——</option>
          <option v-for="ds in (store.datasources as any[])" :key="ds.id" :value="ds.id">
            {{ ds.name }} · {{ ds.db_type }} · {{ ds.host }}:{{ ds.port }}/{{ ds.database }}
          </option>
        </select>
      </div>

      <!-- 风险面板 -->
      <OperationRiskPanel :datasource="selectedDs" context="schema-import" />

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
            Scenario ID(字母/数字/_/-)
          </label>
          <input
            v-model="scenarioId"
            placeholder="如 orders-fixture"
            class="w-full sql-font"
          />
        </div>
        <div>
          <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
            Scenario 名(可选)
          </label>
          <input v-model="scenarioName" placeholder="留空则用 ID" class="w-full" />
        </div>
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
          表名列表(逗号或换行分隔,支持 schema.table)
        </label>
        <textarea
          v-model="tableNamesText"
          rows="4"
          class="w-full sql-font"
          placeholder="如:&#10;ods.orders&#10;ods.users&#10;dwd.fact_sales"
        />
        <div class="mt-1 text-xs text-slate-400">
          解析到 {{ parsedTableNames.length }} 张表
        </div>
      </div>

      <div class="flex items-center gap-3">
        <label class="text-xs flex-1">
          <span class="text-slate-500 font-semibold">默认行数(每表)</span>
          <input v-model.number="defaultRows" type="number" min="1" max="1000000" class="ml-2 w-32" />
        </label>
      </div>

      <div v-if="error" class="rounded p-3 bg-status-error-bg text-status-error text-sm flex items-start gap-2">
        <AlertTriangle class="h-4 w-4 mt-0.5" />
        <span>{{ error }}</span>
      </div>

      <div class="flex flex-wrap gap-2 border-t border-slate-100 pt-4">
        <button
          class="btn btn-outline"
          :disabled="loading || !datasourceId"
          @click="callImport(false)"
        >
          <Eye class="h-4 w-4" />
          {{ loading ? '处理中…' : '👁 预览 yml' }}
        </button>
        <button
          class="btn btn-primary"
          :disabled="loading || !datasourceId || !canSave"
          :title="canSave ? '' : '生产环境不允许保存 yml(仅 sandbox + allow_schema_save=True 可保存)'"
          @click="callImport(true)"
        >
          <Save class="h-4 w-4" />
          {{ loading ? '处理中…' : '💾 保存到 config/scenarios' }}
        </button>
      </div>
    </div>

    <!-- 预览结果 -->
    <div v-if="previewYml" class="card p-5 space-y-3">
      <div class="flex items-center justify-between">
        <h3 class="text-sm font-bold text-slate-800">📄 生成的 yml</h3>
        <button class="btn btn-outline text-xs" @click="copyYml">
          <Copy class="h-3.5 w-3.5" />
          复制
        </button>
      </div>
      <div v-if="savedPath" class="rounded p-2 bg-status-success-bg text-status-success text-xs">
        ✅ 已保存到 <code class="sql-font">config/scenarios/{{ savedPath }}</code>
      </div>
      <pre class="px-3 py-2 bg-slate-50 border border-slate-200 rounded text-[11px] sql-font text-slate-700 overflow-auto max-h-[600px] whitespace-pre">{{ previewYml }}</pre>
    </div>
  </section>
</template>
