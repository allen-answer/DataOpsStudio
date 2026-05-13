<script setup lang="ts">
// Phase 12 切片 5：测试沙盒 admin 视图。
//
// 列 config/scenarios/ 下的 yml → 选一份 → 选 datasource → 一键 materialize
// （生成数据 + DDL/INSERT 到 demo MySQL）+ 一键 record（workloads → CompareTask）。
//
// 后端：
// - GET  /api/scenarios                 列表（含 error 字段标坏文件）
// - GET  /api/scenarios/{id}            详情（tables / anomalies / workloads）
// - POST /api/scenarios/{id}/materialize 生成数据 + 落库
// - POST /api/scenarios/{id}/record     workloads → CompareTask
//
// UI 决策：单视图分两栏（左列表 / 右详情），不再开 tab —— scenario 数量预期 <20，
// 不值得加一层导航。运行结果显示在详情面板下方，紫色 / 绿色 / 红色三态。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  Beaker,
  RefreshCw,
  Play,
  ListChecks,
  AlertCircle,
  CheckCircle2,
  FileWarning,
  Database,
  Microscope,
  ChevronDown,
  ChevronRight,
  Sparkles,
  ShieldCheck,
  Rocket,
  GitBranch,
  Variable,
} from 'lucide-vue-next'
import { apiGet, apiJson } from '../../api'
import { useNoticeStore } from '../../stores/notice'
import { useBootstrapStore } from '../../stores/bootstrap'

interface ScenarioListItem {
  id?: string
  name?: string
  path?: string
  tags?: string[]
  dialect?: string
  error?: string
}

interface ScenarioListResponse {
  items: ScenarioListItem[]
}

interface ColumnDef {
  name: string
  type: string
  pk?: boolean
  gen: string
}

interface ColumnOverride {
  from: string
  rename?: string | null
  transform?: string | null
}

interface IndexDef {
  columns: string[]
  unique?: boolean
  skip?: boolean
  reason?: string
}

interface TableDef {
  name: string
  role: string
  rows: number
  columns: ColumnDef[]
  indexes: IndexDef[]
  derives_from?: string | null
  column_overrides?: ColumnOverride[]
  description?: string
}

interface AnomalyDef {
  kind: string
  table: string
  column?: string | null
  fraction?: number | null
  count?: number | null
  [k: string]: unknown
}

interface WorkloadDef {
  kind: string
  name?: string
  sql?: string
  intentional_issues?: string[]
  expected_optimizations?: string[]
  [k: string]: unknown
}

interface SlowSqlIssue {
  severity: string
  code: string
  message: string
  table?: string
  detail?: string
}

interface SlowSqlSuggestion {
  code: string
  message: string
  sql?: string
}

interface SlowSqlResult {
  dialect: string
  explain_sql: string
  plan: Record<string, unknown>[]
  issues: SlowSqlIssue[]
  suggestions: SlowSqlSuggestion[]
}

interface SlowSqlEnrichReview {
  code: string
  verdict: string
  rationale: string
}

interface SlowSqlEnrichExtra {
  message: string
  sql?: string
  confidence?: string
}

interface SlowSqlEnrichCoverage {
  matched: string[]
  missing: string[]
  coverage_pct: number
}

interface SlowSqlEnrichResult {
  ok: boolean
  summary: string
  issue_review: SlowSqlEnrichReview[]
  extra_suggestions: SlowSqlEnrichExtra[]
  expected_coverage: SlowSqlEnrichCoverage
  provider: string
  model: string
  elapsed_seconds: number
  error?: string
}

interface ScenarioDetail {
  id: string
  name: string
  description?: string
  tags?: string[]
  dialect: string
  seed: number
  tables: TableDef[]
  anomalies: AnomalyDef[]
  workloads: WorkloadDef[]
  variables?: Record<string, string | number | boolean>
}

interface ScenarioDetailResponse {
  scenario: ScenarioDetail
  path: string
}

interface MaterializeTableResult {
  name: string
  schema?: string | null
  rows_inserted: number
  indexes_created?: number
}

interface AiFillReport {
  ok: boolean
  calls: number
  filled_columns: string[]
  filled_descriptions: string[]
  errors: string[]
  skipped_reason: string
}

interface MaterializeResult {
  dialect: string
  schemas_created: string[]
  tables: MaterializeTableResult[]
  warnings: string[]
  rows_generated?: Record<string, number>
  ai_fill?: AiFillReport
}

interface RecordTask {
  id: string
  name: string
  source_id: string
  target_id: string
  source_sql: string
  target_sql: string
  key_columns: string[]
  project_id?: string
}

interface RecordWarning {
  workload_name: string
  reason: string
}

interface LineageRun {
  workload_name: string
  ok: boolean
  run_id: string
  error?: string
}

interface RecordResult {
  tasks: RecordTask[]
  warnings: RecordWarning[]
  lineage_runs?: LineageRun[]
}

interface VerifyItem {
  workload_name: string
  status: 'pass' | 'fail' | 'no_expected' | 'no_task' | 'no_run'
  task_id: string
  task_name: string
  run_id: string
  started_at: string
  expected: Record<string, number>
  actual: Record<string, number>
  deltas: Record<string, number>
  tolerance: Record<string, number>
  match: boolean
}

interface VerifyResult {
  scenario_id: string
  summary: { pass: number; fail: number; skipped: number }
  results: VerifyItem[]
}

interface RunAllRunRecord {
  task_id: string
  task_name: string
  ok: boolean
  summary?: Record<string, number>
  error?: string
}

interface RunAllResult {
  scenario_id: string
  ok: boolean
  error: string
  ai_fill: AiFillReport | null
  materialize: MaterializeResult | null
  record: {
    tasks: { id: string; name: string; project_id?: string }[]
    warnings: RecordWarning[]
    lineage_runs?: LineageRun[]
  }
  runs: RunAllRunRecord[]
  verify: VerifyResult | null
}

const router = useRouter()
const noticeStore = useNoticeStore()
const bootstrapStore = useBootstrapStore()
const { state: bootState } = storeToRefs(bootstrapStore)

const items = ref<ScenarioListItem[]>([])
const loadingList = ref<boolean>(false)
const selectedId = ref<string>('')
const detail = ref<ScenarioDetail | null>(null)
const detailPath = ref<string>('')
const loadingDetail = ref<boolean>(false)

const datasourceId = ref<string>('')
const dropFirst = ref<boolean>(true)
const aiFill = ref<boolean>(false)
const projectId = ref<string>('')

const materializing = ref<boolean>(false)
const recording = ref<boolean>(false)
const verifying = ref<boolean>(false)
const runningAll = ref<boolean>(false)
const materializeResult = ref<MaterializeResult | null>(null)
const recordResult = ref<RecordResult | null>(null)
const verifyResult = ref<VerifyResult | null>(null)
const runAllResult = ref<RunAllResult | null>(null)
const lastError = ref<string>('')

const datasources = computed(() => bootState.value?.datasources || [])

// 只显示能用的 datasource —— 当前 materializer 只支持 mysql
const mysqlDatasources = computed(() =>
  datasources.value.filter((ds: any) => String(ds.db_type || '').toLowerCase().includes('mysql'))
)

const validScenarios = computed(() =>
  items.value.filter((it) => !it.error)
)
const brokenScenarios = computed(() =>
  items.value.filter((it) => !!it.error)
)

const isSelected = (id?: string) => !!id && id === selectedId.value

async function loadList(): Promise<void> {
  loadingList.value = true
  lastError.value = ''
  try {
    const data = await apiGet<ScenarioListResponse>('/api/scenarios')
    items.value = data.items || []
  } catch (e) {
    lastError.value = noticeStore.toErrorMessage(e)
  } finally {
    loadingList.value = false
  }
}

async function selectScenario(id: string): Promise<void> {
  if (!id || id === selectedId.value) return
  selectedId.value = id
  detail.value = null
  detailPath.value = ''
  materializeResult.value = null
  recordResult.value = null
  loadingDetail.value = true
  try {
    const data = await apiGet<ScenarioDetailResponse>(`/api/scenarios/${id}`)
    detail.value = data.scenario
    detailPath.value = data.path
  } catch (e) {
    noticeStore.setNotice(`加载 scenario 失败：${noticeStore.toErrorMessage(e)}`)
  } finally {
    loadingDetail.value = false
  }
}

async function runMaterialize(): Promise<void> {
  if (!selectedId.value || !datasourceId.value) {
    noticeStore.setNotice('请先选 scenario 和 datasource')
    return
  }
  materializing.value = true
  materializeResult.value = null
  lastError.value = ''
  try {
    materializeResult.value = await apiJson<MaterializeResult>(
      `/api/scenarios/${selectedId.value}/materialize`,
      'POST',
      {
        datasource_id: datasourceId.value,
        drop_first: dropFirst.value,
        batch_size: 500,
        ai_fill: aiFill.value,
      },
    )
    noticeStore.setNotice('✨ 数据已落库')
  } catch (e) {
    lastError.value = noticeStore.toErrorMessage(e)
    noticeStore.setNotice(`Materialize 失败：${lastError.value}`)
  } finally {
    materializing.value = false
  }
}

async function runAll(): Promise<void> {
  if (!selectedId.value || !datasourceId.value) {
    noticeStore.setNotice('请先选 scenario 和 datasource')
    return
  }
  runningAll.value = true
  runAllResult.value = null
  // 一键链跑完后把单步结果也同步到面板（让用户看到分步状态）
  materializeResult.value = null
  recordResult.value = null
  verifyResult.value = null
  lastError.value = ''
  try {
    const result = await apiJson<RunAllResult>(
      `/api/scenarios/${selectedId.value}/run-all`,
      'POST',
      {
        datasource_id: datasourceId.value,
        drop_first: dropFirst.value,
        batch_size: 500,
        ai_fill: aiFill.value,
        project_id: projectId.value,
      },
    )
    runAllResult.value = result
    // 同步到各分步面板
    materializeResult.value = result.materialize
    if (result.record?.tasks?.length || result.record?.lineage_runs?.length) {
      recordResult.value = {
        tasks: (result.record.tasks || []).map(t => ({
          id: t.id, name: t.name,
          source_id: '', target_id: '',
          source_sql: '', target_sql: '',
          key_columns: [], project_id: t.project_id || '',
        })),
        warnings: result.record.warnings,
        lineage_runs: result.record.lineage_runs,
      }
    }
    verifyResult.value = result.verify
    if (result.ok) {
      noticeStore.setNotice('🚀 一键链全套完成 · 全部 pass')
    } else {
      noticeStore.setNotice(`一键链完成但有问题：${result.error || '查看下方 verify 结果'}`)
    }
  } catch (e) {
    lastError.value = noticeStore.toErrorMessage(e)
    noticeStore.setNotice(`一键链失败：${lastError.value}`)
  } finally {
    runningAll.value = false
  }
}

async function runVerify(): Promise<void> {
  if (!selectedId.value) return
  verifying.value = true
  verifyResult.value = null
  try {
    verifyResult.value = await apiJson<VerifyResult>(
      `/api/scenarios/${selectedId.value}/verify` + (projectId.value ? `?project_id=${encodeURIComponent(projectId.value)}` : ''),
      'GET',
    )
    const s = verifyResult.value.summary
    noticeStore.setNotice(`回归校验：${s.pass} pass · ${s.fail} fail · ${s.skipped} skipped`)
  } catch (e) {
    noticeStore.setNotice(`Verify 失败：${noticeStore.toErrorMessage(e)}`)
  } finally {
    verifying.value = false
  }
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case 'pass': return 'bg-status-success-bg text-status-success'
    case 'fail': return 'bg-status-error-bg text-status-error'
    case 'no_task':
    case 'no_run':
    case 'no_expected': return 'bg-status-warning-bg text-status-warning'
    default: return 'bg-slate-100 text-slate-600'
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'pass': return '✓ 通过'
    case 'fail': return '✗ 不一致'
    case 'no_expected': return '未声明 expected'
    case 'no_task': return '未 record'
    case 'no_run': return '未跑过'
    default: return status
  }
}

async function runRecord(): Promise<void> {
  if (!selectedId.value || !datasourceId.value) {
    noticeStore.setNotice('请先选 scenario 和 datasource')
    return
  }
  recording.value = true
  recordResult.value = null
  lastError.value = ''
  try {
    recordResult.value = await apiJson<RecordResult>(
      `/api/scenarios/${selectedId.value}/record`,
      'POST',
      { datasource_id: datasourceId.value, project_id: projectId.value },
    )
    noticeStore.setNotice(`✨ 已创建 ${recordResult.value.tasks.length} 个对比任务`)
  } catch (e) {
    lastError.value = noticeStore.toErrorMessage(e)
    noticeStore.setNotice(`Record 失败：${lastError.value}`)
  } finally {
    recording.value = false
  }
}

function gotoTask(taskId: string): void {
  router.push({ path: '/data-compare', query: { task_id: taskId } })
}

function gotoHistory(): void {
  // lineage_script workloads 落进 history 后用户从这里查看 / 跳详情
  router.push({ path: '/history', query: { type: 'lineage' } })
}

// ─── 切片 15：SQL 模板变量替换（前端镜像 app/scenarios/templating.py） ──────
const TEMPLATE_VAR_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g

function renderSql(sql: string | undefined): { text: string; missing: string[] } {
  if (!sql) return { text: '', missing: [] }
  const vars = selected.value?.variables || {}
  const missing = new Set<string>()
  const text = sql.replace(TEMPLATE_VAR_RE, (full, name) => {
    if (Object.prototype.hasOwnProperty.call(vars, name)) {
      const v = vars[name]
      return v === null || v === undefined ? '' : String(v)
    }
    missing.add(name)
    return full
  })
  return { text, missing: Array.from(missing).sort() }
}

// ─── slow-sql 分析（按 workload index 维护，避免多 slow_query 行串台） ───────
const slowSqlResults = ref<Record<number, SlowSqlResult>>({})
const slowSqlAnalyzing = ref<Record<number, boolean>>({})
const slowSqlExpanded = ref<Record<number, boolean>>({})
const slowSqlErrors = ref<Record<number, string>>({})

async function runSlowSqlAnalysis(idx: number, workload: WorkloadDef): Promise<void> {
  if (!workload.sql || !datasourceId.value) {
    noticeStore.setNotice('需要先选 datasource，且 workload 有 sql 字段')
    return
  }
  slowSqlAnalyzing.value = { ...slowSqlAnalyzing.value, [idx]: true }
  slowSqlErrors.value = { ...slowSqlErrors.value, [idx]: '' }
  try {
    const { text: renderedSql } = renderSql(workload.sql)
    const result = await apiJson<SlowSqlResult>('/api/slow-sql/analyze', 'POST', {
      sql: renderedSql,
      datasource_id: datasourceId.value,
    })
    slowSqlResults.value = { ...slowSqlResults.value, [idx]: result }
    slowSqlExpanded.value = { ...slowSqlExpanded.value, [idx]: true }
  } catch (e) {
    slowSqlErrors.value = { ...slowSqlErrors.value, [idx]: noticeStore.toErrorMessage(e) }
    slowSqlExpanded.value = { ...slowSqlExpanded.value, [idx]: true }
  } finally {
    slowSqlAnalyzing.value = { ...slowSqlAnalyzing.value, [idx]: false }
  }
}

function toggleSlowSqlExpansion(idx: number): void {
  slowSqlExpanded.value = { ...slowSqlExpanded.value, [idx]: !slowSqlExpanded.value[idx] }
}

function planColumns(plan: Record<string, unknown>[]): string[] {
  // 取第一行的列名顺序；MySQL EXPLAIN 行结构稳定
  if (!plan.length) return []
  return Object.keys(plan[0])
}

// AI enrichment 按 workload idx 独立维护
const enrichResults = ref<Record<number, SlowSqlEnrichResult>>({})
const enrichLoading = ref<Record<number, boolean>>({})

async function runAiEnrich(idx: number, workload: WorkloadDef): Promise<void> {
  const analysisResult = slowSqlResults.value[idx]
  if (!analysisResult || !workload.sql) {
    noticeStore.setNotice('请先运行规则分析')
    return
  }
  enrichLoading.value = { ...enrichLoading.value, [idx]: true }
  try {
    const { text: renderedSql } = renderSql(workload.sql)
    const result = await apiJson<SlowSqlEnrichResult>('/api/slow-sql/enrich', 'POST', {
      sql: renderedSql,
      plan: analysisResult.plan,
      issues: analysisResult.issues,
      suggestions: analysisResult.suggestions,
      expected_optimizations: workload.expected_optimizations || [],
    })
    enrichResults.value = { ...enrichResults.value, [idx]: result }
    if (!result.ok) {
      noticeStore.setNotice(result.error || 'AI 复核未启用')
    } else {
      const pct = result.expected_coverage.coverage_pct
      noticeStore.setNotice(
        result.expected_coverage.missing.length
          ? `✨ AI 复核完成，覆盖率 ${pct}%`
          : `✨ AI 复核完成`
      )
    }
  } catch (e) {
    noticeStore.setNotice(`AI 复核失败：${noticeStore.toErrorMessage(e)}`)
  } finally {
    enrichLoading.value = { ...enrichLoading.value, [idx]: false }
  }
}

function verdictBadgeClass(verdict: string): string {
  switch (verdict) {
    case 'confirmed': return 'bg-status-success-bg text-status-success'
    case 'false_positive': return 'bg-status-error-bg text-status-error'
    case 'insufficient_info': return 'bg-status-warning-bg text-status-warning'
    default: return 'bg-slate-100 text-slate-600'
  }
}

function confidenceBadgeClass(c?: string): string {
  switch (c) {
    case 'high': return 'bg-status-success-bg text-status-success'
    case 'medium': return 'bg-status-warning-bg text-status-warning'
    case 'low': return 'bg-slate-100 text-slate-600'
    default: return 'bg-slate-100 text-slate-600'
  }
}

function anomalyLabel(a: AnomalyDef): string {
  const parts: string[] = []
  if (a.column) parts.push(a.column)
  if (a.fraction != null) parts.push(`${(a.fraction * 100).toFixed(1)}%`)
  else if (a.count != null) parts.push(`${a.count} 条`)
  return parts.join(' · ')
}

function totalRows(d: ScenarioDetail): number {
  return d.tables.reduce((sum, t) => sum + (t.rows || 0), 0)
}

onMounted(async () => {
  await loadList()
  // 默认选第一个有效 scenario
  if (validScenarios.value.length && !selectedId.value) {
    const firstId = validScenarios.value[0].id
    if (firstId) await selectScenario(firstId)
  }
  // 默认选第一个 MySQL datasource
  if (mysqlDatasources.value.length && !datasourceId.value) {
    datasourceId.value = (mysqlDatasources.value[0] as any).id
  }
})
</script>

<template>
  <section class="space-y-6">
    <div class="flex items-end justify-between">
      <div>
        <h2 class="text-2xl font-bold text-slate-800 flex items-center gap-2">
          <Beaker class="h-7 w-7 text-primary" />
          测试沙盒
        </h2>
        <p class="mt-1 text-sm text-slate-500">
          用 scenario 模板一键生成测试数据 + 对比任务。Phase 12 · MVP（仅 MySQL）。
        </p>
      </div>
      <button class="btn btn-outline" :disabled="loadingList" @click="loadList">
        <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': loadingList }" />
        刷新列表
      </button>
    </div>

    <div v-if="lastError" class="card border-status-error bg-status-error-bg p-4 flex items-start gap-3">
      <AlertCircle class="h-5 w-5 text-status-error flex-shrink-0 mt-0.5" />
      <div class="text-sm text-status-error">{{ lastError }}</div>
    </div>

    <div v-if="brokenScenarios.length" class="card border-status-warning bg-status-warning-bg p-4">
      <div class="text-sm font-medium text-status-warning flex items-center gap-2">
        <FileWarning class="h-4 w-4" /> 有 {{ brokenScenarios.length }} 份 scenario yml 解析失败
      </div>
      <ul class="mt-2 space-y-1 text-xs text-status-warning">
        <li v-for="b in brokenScenarios" :key="b.path">
          <code class="sql-font">{{ b.path }}</code> — {{ b.error }}
        </li>
      </ul>
    </div>

    <div class="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
      <!-- 左：scenario 列表 -->
      <aside class="space-y-3">
        <div class="text-xs uppercase tracking-wider text-slate-500 font-bold">
          可用模板（{{ validScenarios.length }}）
        </div>
        <div v-if="loadingList" class="muted text-sm">加载中…</div>
        <div v-else-if="!validScenarios.length" class="card p-4 text-sm text-slate-500">
          <p>config/scenarios/ 下无可用 yml。</p>
          <p class="mt-2 text-xs">把 example 复制成 `*.yml` 即可上架（参考 orders-recon.example.yml）。</p>
        </div>
        <button
          v-for="it in validScenarios"
          :key="it.path"
          class="w-full text-left card p-4 transition-all hover:border-primary hover:shadow-md"
          :class="isSelected(it.id) ? 'border-primary shadow-md ring-2 ring-primary/20' : ''"
          @click="selectScenario(it.id || '')"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="font-medium text-slate-800">{{ it.name || it.id }}</div>
            <span v-if="it.dialect" class="pill bg-slate-100 text-slate-600">{{ it.dialect }}</span>
          </div>
          <div class="mt-1 text-xs text-slate-500 sql-font">{{ it.id }}</div>
          <div v-if="it.tags?.length" class="mt-2 flex flex-wrap gap-1">
            <span v-for="t in it.tags" :key="t" class="pill bg-primary-light text-primary">{{ t }}</span>
          </div>
        </button>
      </aside>

      <!-- 右：detail + action -->
      <div class="space-y-6">
        <div v-if="loadingDetail" class="card p-6 muted text-center">加载详情中…</div>
        <div v-else-if="!detail" class="card p-12 text-center text-slate-500">
          <Beaker class="h-12 w-12 mx-auto text-slate-300" />
          <p class="mt-3 text-sm">选一份 scenario 模板开始</p>
        </div>

        <template v-else>
          <!-- 头部 + 操作 -->
          <div class="card p-6">
            <div class="flex items-start justify-between gap-4 mb-4">
              <div>
                <h3 class="text-xl font-bold text-slate-800">{{ detail.name }}</h3>
                <p class="mt-1 text-sm text-slate-500">{{ detail.description }}</p>
                <div class="mt-2 text-xs text-slate-400 sql-font">
                  {{ detailPath }} · seed={{ detail.seed }} · {{ totalRows(detail) }} 行预计生成
                </div>
              </div>
              <div class="flex flex-wrap gap-1">
                <span v-for="t in (detail.tags || [])" :key="t" class="pill bg-slate-100 text-slate-600">
                  {{ t }}
                </span>
              </div>
            </div>

            <!-- 选 datasource + 选项 -->
            <div class="grid grid-cols-1 md:grid-cols-[1fr_auto_auto] gap-3 items-end border-t border-slate-200 pt-4">
              <div>
                <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
                  <Database class="h-3 w-3 inline" /> 目标 datasource（MySQL）
                </label>
                <select v-model="datasourceId" class="w-full">
                  <option value="" disabled>—— 选一个 ——</option>
                  <option
                    v-for="ds in mysqlDatasources"
                    :key="(ds as any).id"
                    :value="(ds as any).id"
                  >
                    {{ (ds as any).name }} · {{ (ds as any).host }}:{{ (ds as any).port }}
                  </option>
                </select>
                <p v-if="!mysqlDatasources.length" class="mt-1 text-xs text-status-warning">
                  无可用 MySQL datasource —— 先去「数据源」页加一个。
                </p>
              </div>
              <label class="flex items-center gap-2 text-sm pb-1.5">
                <input type="checkbox" v-model="dropFirst" />
                <span>DROP 已存在</span>
              </label>
              <label class="flex items-center gap-2 text-sm pb-1.5" title="先走 LLM 把 realistic 列填业务化样本池，再生成数据">
                <input type="checkbox" v-model="aiFill" />
                <span class="flex items-center gap-1">
                  <Sparkles class="h-3.5 w-3.5 text-primary" />
                  AI 填血肉
                </span>
              </label>
              <div>
                <label class="block text-xs uppercase tracking-wider text-slate-500 font-bold mb-1">
                  项目空间（可选）
                </label>
                <input v-model="projectId" placeholder="留空 = 默认" class="w-32" />
              </div>
            </div>

            <div class="mt-4 flex flex-wrap gap-3">
              <button
                class="btn btn-primary"
                :disabled="!datasourceId || runningAll"
                @click="runAll"
                title="fill → generate → materialize → record → run tasks → verify 一气呵成"
              >
                <Rocket class="h-4 w-4" :class="{ 'animate-pulse': runningAll }" />
                {{ runningAll ? '一键链跑中…' : '🚀 一键全套' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="!datasourceId || materializing"
                @click="runMaterialize"
              >
                <Play class="h-4 w-4" :class="{ 'animate-pulse': materializing }" />
                {{ materializing ? '生成中…' : '仅生成数据' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="!datasourceId || recording"
                @click="runRecord"
              >
                <ListChecks class="h-4 w-4" />
                {{ recording ? '建任务中…' : '建对比任务' }}
              </button>
              <button
                class="btn btn-outline"
                :disabled="verifying"
                @click="runVerify"
                title="对比 yml expected vs actual run summary，把 scenario 当回归 fixture 用"
              >
                <ShieldCheck class="h-4 w-4" />
                {{ verifying ? '校验中…' : '回归校验' }}
              </button>
            </div>
          </div>

          <!-- 三栏 schema breakdown -->
          <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div class="card p-4">
              <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-3">
                表（{{ detail.tables.length }}）
              </div>
              <ul class="space-y-2">
                <li v-for="t in detail.tables" :key="t.name" class="text-sm">
                  <div class="flex items-center gap-2">
                    <span class="font-medium sql-font text-slate-800">{{ t.name }}</span>
                    <span class="pill bg-tag-source-bg text-tag-source">{{ t.role }}</span>
                  </div>
                  <div class="text-xs text-slate-500 mt-0.5">
                    {{ t.rows }} 行
                    <span v-if="t.derives_from"> · 派生自 {{ t.derives_from }}</span>
                    <span v-if="t.columns?.length"> · {{ t.columns.length }} 列</span>
                  </div>
                </li>
              </ul>
            </div>

            <div class="card p-4">
              <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-3">
                偏差（{{ detail.anomalies.length }}）
              </div>
              <ul class="space-y-2">
                <li v-for="(a, idx) in detail.anomalies" :key="idx" class="text-sm">
                  <div class="flex items-center gap-2">
                    <span class="pill bg-status-warning-bg text-status-warning">{{ a.kind }}</span>
                    <span class="sql-font text-slate-600">{{ a.table }}</span>
                  </div>
                  <div class="text-xs text-slate-500 mt-0.5">{{ anomalyLabel(a) }}</div>
                </li>
                <li v-if="!detail.anomalies.length" class="text-sm text-slate-400">无偏差注入</li>
              </ul>
            </div>

            <div class="card p-4">
              <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-3">
                工作负载（{{ detail.workloads.length }}）
              </div>
              <ul class="space-y-2">
                <li v-for="(w, idx) in detail.workloads" :key="idx" class="text-sm">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="pill bg-primary-light text-primary">{{ w.kind }}</span>
                    <span class="text-slate-800">{{ w.name || '—' }}</span>
                    <div v-if="w.kind === 'slow_query' && w.sql" class="ml-auto flex items-center gap-2">
                      <button
                        class="text-xs text-primary hover:underline flex items-center gap-0.5 disabled:text-slate-400"
                        :disabled="!datasourceId || slowSqlAnalyzing[idx]"
                        @click="runSlowSqlAnalysis(idx, w)"
                      >
                        <Microscope class="h-3.5 w-3.5" :class="{ 'animate-pulse': slowSqlAnalyzing[idx] }" />
                        {{ slowSqlAnalyzing[idx] ? '分析中…' : '分析' }}
                      </button>
                      <button
                        v-if="slowSqlResults[idx]"
                        class="text-xs text-primary hover:underline flex items-center gap-0.5 disabled:text-slate-400"
                        :disabled="enrichLoading[idx]"
                        @click="runAiEnrich(idx, w)"
                      >
                        <Sparkles class="h-3.5 w-3.5" :class="{ 'animate-pulse': enrichLoading[idx] }" />
                        {{ enrichLoading[idx] ? 'AI 复核中…' : 'AI 复核' }}
                      </button>
                    </div>
                    <button
                      v-else-if="w.kind === 'slow_query' && slowSqlResults[idx]"
                      class="ml-auto text-xs text-slate-500 flex items-center gap-0.5"
                      @click="toggleSlowSqlExpansion(idx)"
                    >
                      <component :is="slowSqlExpanded[idx] ? ChevronDown : ChevronRight" class="h-3.5 w-3.5" />
                      {{ slowSqlExpanded[idx] ? '收起' : '展开' }}
                    </button>
                  </div>
                </li>
                <li v-if="!detail.workloads.length" class="text-sm text-slate-400">无工作负载</li>
              </ul>

              <!-- 切片 15：模板变量（仅当 yml 声明了 variables: 块时显示） -->
              <div
                v-if="detail.variables && Object.keys(detail.variables).length"
                class="mt-4 pt-3 border-t border-line"
              >
                <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-2 flex items-center gap-1.5">
                  <Variable class="h-3.5 w-3.5" />
                  模板变量
                  <span class="ml-1 text-[10px] font-normal normal-case tracking-normal text-slate-400">
                    workload.sql 里 <code class="sql-font">{{ '{{name}}' }}</code> 占位符会渲染成此处值
                  </span>
                </div>
                <ul class="space-y-1 text-xs">
                  <li
                    v-for="(value, name) in detail.variables"
                    :key="name"
                    class="flex items-center gap-2 sql-font"
                  >
                    <span class="text-primary font-medium">{{ name }}</span>
                    <span class="text-slate-400">→</span>
                    <span class="text-slate-700">{{ value }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>

          <!-- 一键链 banner（仅 run-all 跑过时显示） -->
          <div
            v-if="runAllResult"
            class="card p-4 border-2"
            :class="runAllResult.ok ? 'border-status-success bg-status-success-bg' : 'border-status-error bg-status-error-bg'"
          >
            <div class="flex items-center gap-2 text-sm font-bold">
              <Rocket class="h-5 w-5" :class="runAllResult.ok ? 'text-status-success' : 'text-status-error'" />
              <span :class="runAllResult.ok ? 'text-status-success' : 'text-status-error'">
                {{ runAllResult.ok ? '一键链全套通过' : '一键链有失败步骤' }}
              </span>
            </div>
            <div class="mt-2 text-xs text-slate-700 flex flex-wrap gap-x-4 gap-y-1">
              <span v-if="runAllResult.ai_fill">
                AI 填充：{{ runAllResult.ai_fill.ok ? `${runAllResult.ai_fill.calls} 调用` : '跳过' }}
              </span>
              <span v-if="runAllResult.materialize">
                落库：{{ runAllResult.materialize.tables?.length || 0 }} 表
              </span>
              <span v-if="runAllResult.record">
                建任务：{{ runAllResult.record.tasks?.length || 0 }} 个
              </span>
              <span>
                运行：{{ runAllResult.runs.filter(r => r.ok).length }} / {{ runAllResult.runs.length }} ok
              </span>
              <span v-if="runAllResult.verify">
                校验：{{ runAllResult.verify.summary.pass }} pass · {{ runAllResult.verify.summary.fail }} fail · {{ runAllResult.verify.summary.skipped }} skipped
              </span>
            </div>
            <div v-if="runAllResult.error" class="mt-2 text-xs text-status-error">
              错误：{{ runAllResult.error }}
            </div>
            <div v-if="runAllResult.runs.some(r => !r.ok)" class="mt-2 text-xs">
              <div class="font-medium text-status-error mb-1">失败的 run：</div>
              <ul class="ml-2 space-y-0.5 text-slate-700">
                <li
                  v-for="r in runAllResult.runs.filter(x => !x.ok)"
                  :key="r.task_id"
                >
                  <span class="sql-font">{{ r.task_name }}</span> — {{ r.error }}
                </li>
              </ul>
            </div>
          </div>

          <!-- verify 回归校验结果 -->
          <div v-if="verifyResult" class="card border-slate-200 p-4">
            <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
              <div class="text-sm font-bold text-slate-800 flex items-center gap-2">
                <ShieldCheck class="h-4 w-4 text-primary" />
                回归校验
              </div>
              <div class="flex gap-2 text-xs">
                <span class="pill bg-status-success-bg text-status-success">
                  {{ verifyResult.summary.pass }} pass
                </span>
                <span class="pill bg-status-error-bg text-status-error">
                  {{ verifyResult.summary.fail }} fail
                </span>
                <span class="pill bg-status-warning-bg text-status-warning">
                  {{ verifyResult.summary.skipped }} skipped
                </span>
              </div>
            </div>
            <ul class="space-y-3">
              <li
                v-for="(r, i) in verifyResult.results"
                :key="i"
                class="rounded-lg border border-slate-200 p-3"
              >
                <div class="flex items-center justify-between flex-wrap gap-2">
                  <div class="flex items-center gap-2">
                    <span class="pill text-xs" :class="statusBadgeClass(r.status)">
                      {{ statusLabel(r.status) }}
                    </span>
                    <span class="font-medium text-slate-800 sql-font">{{ r.workload_name }}</span>
                  </div>
                  <button
                    v-if="r.task_id"
                    class="text-xs text-primary hover:underline"
                    @click="gotoTask(r.task_id)"
                  >打开任务 →</button>
                </div>
                <div
                  v-if="r.status === 'pass' || r.status === 'fail'"
                  class="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs"
                >
                  <div
                    v-for="key in Object.keys(r.expected)"
                    :key="key"
                    class="rounded p-2 bg-slate-50"
                  >
                    <div class="flex items-center justify-between">
                      <span class="text-slate-500 sql-font">{{ key }}</span>
                      <span
                        v-if="(r.tolerance?.[key] || 0) > 0"
                        class="px-1 rounded bg-slate-200 text-slate-600 sql-font text-[10px]"
                        title="允许容差"
                      >±{{ r.tolerance[key] }}</span>
                    </div>
                    <div class="mt-0.5 text-slate-700">
                      expected {{ r.expected[key] }} →
                      <span
                        :class="Math.abs(r.deltas[key]) <= (r.tolerance?.[key] || 0) ? 'text-status-success font-medium' : 'text-status-error font-medium'"
                      >
                        actual {{ r.actual[key] || 0 }}
                        <span v-if="r.deltas[key] !== 0" class="sql-font">
                          ({{ r.deltas[key] > 0 ? '+' : '' }}{{ r.deltas[key] }})
                        </span>
                      </span>
                    </div>
                  </div>
                </div>
                <div
                  v-else-if="r.status === 'no_run' && r.task_id"
                  class="mt-2 text-xs text-slate-500"
                >
                  task <span class="sql-font">{{ r.task_name }}</span> 尚未跑过；
                  <button
                    class="text-primary hover:underline"
                    @click="gotoTask(r.task_id)"
                  >去工作台运行 →</button>
                </div>
                <div
                  v-else-if="r.status === 'no_task'"
                  class="mt-2 text-xs text-slate-500"
                >
                  scenario 还没 record 对应的 CompareTask。点上方「建对比任务」补一次。
                </div>
                <div
                  v-else-if="r.status === 'no_expected'"
                  class="mt-2 text-xs text-slate-500"
                >
                  yml workload 没写 <code class="sql-font">expected:</code> 块；
                  补上后即可纳入回归。
                </div>
              </li>
            </ul>
          </div>

          <!-- slow_query 分析结果：期望 vs 实际并排 -->
          <template v-for="(w, idx) in detail.workloads" :key="`slow-${idx}`">
            <div
              v-if="w.kind === 'slow_query' && (slowSqlResults[idx] || slowSqlErrors[idx]) && slowSqlExpanded[idx]"
              class="card p-4 space-y-4"
            >
              <div class="flex items-center justify-between">
                <div class="text-sm font-bold text-slate-800 flex items-center gap-2">
                  <Microscope class="h-4 w-4 text-primary" />
                  慢 SQL 分析 · <span class="sql-font">{{ w.name }}</span>
                </div>
                <button
                  class="text-xs text-slate-500 hover:text-slate-700"
                  @click="toggleSlowSqlExpansion(idx)"
                >
                  收起
                </button>
              </div>

              <!-- 错误态 -->
              <div
                v-if="slowSqlErrors[idx]"
                class="rounded-lg border border-status-error bg-status-error-bg p-3 text-sm text-status-error flex items-start gap-2"
              >
                <AlertCircle class="h-4 w-4 mt-0.5 flex-shrink-0" />
                <span>{{ slowSqlErrors[idx] }}</span>
              </div>

              <!-- 成功态：左 expected / 右 actual -->
              <div v-if="slowSqlResults[idx]" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div class="text-xs uppercase tracking-wider text-slate-500 font-bold mb-2">
                    🎯 期望发现（来自 yml）
                  </div>
                  <div v-if="w.intentional_issues?.length">
                    <div class="text-xs font-medium text-slate-600 mb-1">设计的问题：</div>
                    <ul class="space-y-1 text-xs text-slate-700">
                      <li v-for="(it, i) in w.intentional_issues" :key="`ii-${i}`">• {{ it }}</li>
                    </ul>
                  </div>
                  <div v-if="w.expected_optimizations?.length" class="mt-3">
                    <div class="text-xs font-medium text-slate-600 mb-1">期望优化：</div>
                    <ul class="space-y-1 text-xs text-slate-700">
                      <li v-for="(it, i) in w.expected_optimizations" :key="`eo-${i}`">• {{ it }}</li>
                    </ul>
                  </div>
                  <div v-if="!w.intentional_issues?.length && !w.expected_optimizations?.length"
                    class="text-xs text-slate-400">
                    workload 未声明 intentional_issues / expected_optimizations
                  </div>
                </div>

                <div class="rounded-lg border border-primary bg-primary-light/30 p-3">
                  <div class="text-xs uppercase tracking-wider text-primary font-bold mb-2">
                    🔬 EXPLAIN 实测
                  </div>
                  <div v-if="slowSqlResults[idx].issues.length">
                    <div class="text-xs font-medium text-slate-700 mb-1">实测发现问题：</div>
                    <ul class="space-y-1 text-xs text-slate-700">
                      <li v-for="(it, i) in slowSqlResults[idx].issues" :key="`is-${i}`">
                        <span class="pill bg-status-warning-bg text-status-warning text-[10px]">{{ it.code }}</span>
                        {{ it.message }}
                      </li>
                    </ul>
                  </div>
                  <div v-else class="text-xs text-slate-500">✓ 规则层未发现问题</div>
                  <div v-if="slowSqlResults[idx].suggestions.length" class="mt-3">
                    <div class="text-xs font-medium text-slate-700 mb-1">优化建议：</div>
                    <ul class="space-y-1 text-xs text-slate-700">
                      <li v-for="(s, i) in slowSqlResults[idx].suggestions" :key="`sg-${i}`">
                        ✨ {{ s.message }}
                      </li>
                    </ul>
                  </div>
                </div>
              </div>

              <!-- AI enrichment 结果（如果已跑） -->
              <div v-if="enrichResults[idx]" class="rounded-lg border-2 border-primary p-3 space-y-3 bg-white">
                <div class="flex items-center justify-between">
                  <div class="text-sm font-bold text-primary flex items-center gap-1.5">
                    <Sparkles class="h-4 w-4" />
                    AI 复核 · <span class="sql-font text-xs text-slate-500">{{ enrichResults[idx].provider }}/{{ enrichResults[idx].model }}</span>
                  </div>
                  <span
                    v-if="enrichResults[idx].ok && enrichResults[idx].expected_coverage.coverage_pct > 0"
                    class="pill"
                    :class="enrichResults[idx].expected_coverage.coverage_pct >= 80 ? 'bg-status-success-bg text-status-success' : enrichResults[idx].expected_coverage.coverage_pct >= 40 ? 'bg-status-warning-bg text-status-warning' : 'bg-status-error-bg text-status-error'"
                  >
                    覆盖 {{ enrichResults[idx].expected_coverage.coverage_pct }}%
                  </span>
                </div>

                <!-- 未启用 -->
                <div
                  v-if="!enrichResults[idx].ok"
                  class="text-xs text-slate-500 italic"
                >
                  {{ enrichResults[idx].error || 'AI provider 未启用，请在 admin → AI 配置中开启' }}
                </div>

                <!-- 已启用 -->
                <template v-else>
                  <div v-if="enrichResults[idx].summary" class="text-sm text-slate-700">
                    {{ enrichResults[idx].summary }}
                  </div>

                  <div v-if="enrichResults[idx].issue_review.length" class="space-y-1">
                    <div class="text-xs font-bold text-slate-600">规则 issue 复核：</div>
                    <ul class="space-y-1 text-xs">
                      <li v-for="(rev, i) in enrichResults[idx].issue_review" :key="`rev-${i}`" class="flex items-start gap-2">
                        <span class="pill text-[10px]" :class="verdictBadgeClass(rev.verdict)">{{ rev.verdict }}</span>
                        <span class="text-slate-700"><span class="sql-font text-slate-500">{{ rev.code }}</span> — {{ rev.rationale }}</span>
                      </li>
                    </ul>
                  </div>

                  <div v-if="enrichResults[idx].extra_suggestions.length" class="space-y-1">
                    <div class="text-xs font-bold text-slate-600">AI 补充建议：</div>
                    <ul class="space-y-2 text-xs">
                      <li v-for="(ex, i) in enrichResults[idx].extra_suggestions" :key="`extra-${i}`">
                        <div class="flex items-start gap-2">
                          <span class="pill text-[10px]" :class="confidenceBadgeClass(ex.confidence)">{{ ex.confidence || '—' }}</span>
                          <span class="text-slate-700">{{ ex.message }}</span>
                        </div>
                        <pre
                          v-if="ex.sql"
                          class="mt-1 ml-12 px-2 py-1 bg-slate-50 border border-slate-200 rounded text-[11px] sql-font text-slate-700 whitespace-pre-wrap"
                        >{{ ex.sql }}</pre>
                      </li>
                    </ul>
                  </div>

                  <div
                    v-if="enrichResults[idx].expected_coverage.matched.length || enrichResults[idx].expected_coverage.missing.length"
                    class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs"
                  >
                    <div v-if="enrichResults[idx].expected_coverage.matched.length">
                      <div class="font-bold text-status-success mb-0.5">✓ 命中期望（{{ enrichResults[idx].expected_coverage.matched.length }}）</div>
                      <ul class="space-y-0.5 text-slate-700">
                        <li v-for="(m, i) in enrichResults[idx].expected_coverage.matched" :key="`m-${i}`">• {{ m }}</li>
                      </ul>
                    </div>
                    <div v-if="enrichResults[idx].expected_coverage.missing.length">
                      <div class="font-bold text-status-error mb-0.5">✗ 仍有遗漏（{{ enrichResults[idx].expected_coverage.missing.length }}）</div>
                      <ul class="space-y-0.5 text-slate-700">
                        <li v-for="(m, i) in enrichResults[idx].expected_coverage.missing" :key="`mi-${i}`">• {{ m }}</li>
                      </ul>
                    </div>
                  </div>
                </template>
              </div>

              <!-- EXPLAIN plan 原始行 -->
              <div v-if="slowSqlResults[idx]?.plan?.length" class="rounded-lg border border-slate-200 bg-white overflow-x-auto">
                <div class="px-3 py-2 text-xs font-bold text-slate-600 border-b border-slate-200">
                  EXPLAIN 输出（{{ slowSqlResults[idx].plan.length }} 行）
                </div>
                <table class="w-full text-xs sql-font">
                  <thead>
                    <tr class="bg-slate-50">
                      <th
                        v-for="col in planColumns(slowSqlResults[idx].plan)"
                        :key="col"
                        class="text-left px-2 py-1.5 font-medium text-slate-600 border-b border-slate-200"
                      >
                        {{ col }}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="(row, ri) in slowSqlResults[idx].plan"
                      :key="ri"
                      class="border-b border-slate-100 hover:bg-slate-50"
                    >
                      <td
                        v-for="col in planColumns(slowSqlResults[idx].plan)"
                        :key="col"
                        class="px-2 py-1.5 text-slate-700"
                      >
                        {{ row[col] == null ? '·' : String(row[col]) }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </template>

          <!-- materialize result -->
          <div v-if="materializeResult" class="card border-status-success bg-status-success-bg p-4">
            <div class="flex items-center gap-2 text-status-success font-bold text-sm">
              <CheckCircle2 class="h-5 w-5" /> 数据已落库
            </div>
            <div
              v-if="materializeResult.ai_fill"
              class="mt-2 rounded bg-white p-2 text-xs flex items-start gap-2"
              :class="materializeResult.ai_fill.ok ? 'border border-primary' : 'border border-slate-200'"
            >
              <Sparkles class="h-3.5 w-3.5 mt-0.5 flex-shrink-0" :class="materializeResult.ai_fill.ok ? 'text-primary' : 'text-slate-400'" />
              <div class="flex-1">
                <div class="font-medium" :class="materializeResult.ai_fill.ok ? 'text-primary' : 'text-slate-500'">
                  AI 填血肉
                  <template v-if="materializeResult.ai_fill.ok">
                    · {{ materializeResult.ai_fill.calls }} 个 LLM 调用 ·
                    填了 {{ materializeResult.ai_fill.filled_columns.length }} 列样本池 +
                    {{ materializeResult.ai_fill.filled_descriptions.length }} 表描述
                  </template>
                  <template v-else>· 跳过：{{ materializeResult.ai_fill.skipped_reason }}</template>
                </div>
                <div v-if="materializeResult.ai_fill.errors.length" class="mt-1 text-status-warning">
                  ⚠ {{ materializeResult.ai_fill.errors.length }} 项失败：
                  <span class="sql-font">{{ materializeResult.ai_fill.errors.join(' / ') }}</span>
                </div>
                <div v-if="materializeResult.ai_fill.filled_columns.length" class="mt-1 text-slate-500">
                  <span class="sql-font">{{ materializeResult.ai_fill.filled_columns.join(', ') }}</span>
                </div>
              </div>
            </div>
            <div class="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
              <div
                v-for="(rows, name) in (materializeResult.rows_generated || {})"
                :key="name"
                class="bg-white rounded p-2"
              >
                <div class="sql-font text-slate-800">{{ name }}</div>
                <div class="text-slate-500 mt-0.5">生成 {{ rows }} 行</div>
                <div
                  v-for="t in materializeResult.tables.filter((x: MaterializeTableResult) => x.name === name)"
                  :key="t.name"
                  class="text-slate-500"
                >
                  落库 {{ t.rows_inserted }} 行 · {{ t.indexes_created || 0 }} 索引
                </div>
              </div>
            </div>
            <div v-if="materializeResult.warnings?.length" class="mt-2 text-xs text-status-warning">
              warnings: {{ materializeResult.warnings.join(' / ') }}
            </div>
          </div>

          <!-- record result -->
          <div v-if="recordResult" class="card border-primary p-4">
            <div class="flex items-center gap-2 text-primary font-bold text-sm">
              <ListChecks class="h-5 w-5" /> 已创建 {{ recordResult.tasks.length }} 个对比任务
            </div>
            <ul v-if="recordResult.tasks.length" class="mt-3 space-y-1 text-sm">
              <li
                v-for="t in recordResult.tasks"
                :key="t.id"
                class="flex items-center justify-between bg-white rounded p-2"
              >
                <span class="sql-font text-slate-800">{{ t.name }}</span>
                <button class="text-xs text-primary hover:underline" @click="gotoTask(t.id)">
                  打开任务 →
                </button>
              </li>
            </ul>
            <div v-if="recordResult.lineage_runs?.length" class="mt-3">
              <div class="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1">
                <GitBranch class="h-4 w-4 text-primary" />
                血缘脚本入库（{{ recordResult.lineage_runs.length }}）
              </div>
              <ul class="space-y-1 text-sm">
                <li
                  v-for="(r, i) in recordResult.lineage_runs"
                  :key="i"
                  class="flex items-center justify-between bg-white rounded p-2"
                >
                  <div class="flex items-center gap-2 flex-1 min-w-0">
                    <span
                      class="pill text-[10px]"
                      :class="r.ok ? 'bg-status-success-bg text-status-success' : 'bg-status-error-bg text-status-error'"
                    >{{ r.ok ? '✓ 已分析' : '✗ 失败' }}</span>
                    <span class="sql-font text-slate-800 truncate">{{ r.workload_name }}</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <code v-if="r.run_id" class="sql-font text-xs text-slate-400">{{ r.run_id.slice(-8) }}</code>
                    <button
                      v-if="r.ok"
                      class="text-xs text-primary hover:underline whitespace-nowrap"
                      @click="gotoHistory"
                    >
                      查看历史 →
                    </button>
                    <span v-else class="text-xs text-status-error">{{ r.error }}</span>
                  </div>
                </li>
              </ul>
            </div>
            <div v-if="recordResult.warnings?.length" class="mt-3 text-xs text-status-warning">
              <div class="font-medium mb-1">⚠ 部分 workload 被跳过：</div>
              <ul class="ml-2 space-y-0.5">
                <li v-for="(w, idx) in recordResult.warnings" :key="idx">
                  <code class="sql-font">{{ w.workload_name }}</code> — {{ w.reason }}
                </li>
              </ul>
            </div>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>
