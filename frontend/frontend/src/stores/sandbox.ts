/**
 * @deprecated Phase 14 #3 Round 2 起,views 和子组件应通过 facade stores
 * 访问:
 *   - useSqlDiagnosisStore (stores/sqlDiagnosis.ts) — /sql-diagnosis 用
 *   - useScenarioLabStore  (stores/scenarioLab.ts)  — /scenario-lab 用
 *   - useSchemaImportStore (stores/schemaImport.ts) — /schema-import 用
 *
 * 本文件仍是 backing — 三个 facade 引用同一份 reactive state 避免双向同步。
 * 长期目标:把字段真正拆到各 facade,delete 此 file。当前作为过渡保留。
 *
 * 历史:Phase 14 P2 把 SqlOptimizeView 1689 行的 state + actions 全收口到这。
 * Phase 14 #3 Round 1 拆 3 个 view,但 view 还在 import sandbox。
 * Phase 14 #3 Round 2 引入 facade stores 让 view import 干净。
 */
import { computed, ref } from 'vue'
import { defineStore, storeToRefs } from 'pinia'

import { router } from '../router'
import { apiGet, apiJson } from '../api'
import { useNoticeStore } from './notice'
import { useBootstrapStore } from './bootstrap'

import type {
  AiFillReport, AnomalyDef, ImportFromDsResult, MaterializeResult, PlanDiffResult,
  PlanHistoryItem, RecordResult, RunAllResult, ScenarioDetail, ScenarioDetailResponse,
  ScenarioListItem, ScenarioListResponse, SlowSqlEnrichResult, SlowSqlResult, StepId,
  VerifyResult, WorkloadDef,
} from '../types/sandbox'


const TEMPLATE_VAR_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g


export const useSandboxStore = defineStore('sandbox', () => {
  // ─── core state ─────────────────────────────────────────────────────────
  const items = ref<ScenarioListItem[]>([])
  const loadingList = ref(false)
  const selectedId = ref('')
  const detail = ref<ScenarioDetail | null>(null)
  const detailPath = ref('')
  const loadingDetail = ref(false)

  const datasourceId = ref('')
  const dropFirst = ref(true)
  const aiFill = ref(false)
  const projectId = ref('')

  const materializing = ref(false)
  const recording = ref(false)
  const verifying = ref(false)
  const runningAll = ref(false)
  const materializeResult = ref<MaterializeResult | null>(null)
  const recordResult = ref<RecordResult | null>(null)
  const verifyResult = ref<VerifyResult | null>(null)
  const runAllResult = ref<RunAllResult | null>(null)
  const lastError = ref('')

  // slow-sql / plan diff / AI enrich(按 workload idx 维护)
  const slowSqlResults = ref<Record<number, SlowSqlResult>>({})
  const slowSqlAnalyzing = ref<Record<number, boolean>>({})
  const slowSqlExpanded = ref<Record<number, boolean>>({})
  const slowSqlErrors = ref<Record<number, string>>({})
  const planDiffs = ref<Record<number, PlanDiffResult | null>>({})
  const planDiffLoading = ref<Record<number, boolean>>({})
  const planDiffErrors = ref<Record<number, string>>({})
  const enrichResults = ref<Record<number, SlowSqlEnrichResult>>({})
  const enrichLoading = ref<Record<number, boolean>>({})

  // import from datasource(P1-1 UI 状态)
  const importDialogOpen = ref(false)
  const importing = ref(false)
  const importResult = ref<ImportFromDsResult | null>(null)
  const importError = ref('')
  const importForm = ref({
    datasource_id: '', table_names: '', scenario_id: '',
    scenario_name: '', default_rows: 1000, save: true,
  })

  // Phase 14 修缮:模式 toggle + 「快速优化」mode(默认)状态。
  // quick mode 不依赖 scenario 模板,直接粘 SQL + 选 datasource 跑 EXPLAIN。
  // 复用 /api/slow-sql/analyze + /enrich + plan-history + plan-diff 后端。
  const viewMode = ref<'quick' | 'template'>('quick')
  const quickSql = ref('')
  const quickDatasourceId = ref('')
  const quickTagScenarioId = ref('')  // 可选 history 归组标签
  const quickAnalyzing = ref(false)
  const quickEnriching = ref(false)
  const quickPlanDiffLoading = ref(false)
  const quickResult = ref<SlowSqlResult | null>(null)
  const quickEnrichResult = ref<SlowSqlEnrichResult | null>(null)
  const quickPlanDiff = ref<PlanDiffResult | null>(null)
  const quickPlanHistory = ref<PlanHistoryItem[]>([])
  // Phase 14 #3 — view 可注入异步 confirm callback (替换 confirm())。
  // SqlDiagnosisView 在 onMounted 注入 OperationPreviewModal,其它入口走 fallback。
  const confirmAnalyzePromise = ref<(() => Promise<boolean>) | null>(null)
  const quickError = ref('')
  const quickPlanDiffError = ref('')

  // ─── derived ───────────────────────────────────────────────────────────
  const bootstrapStore = useBootstrapStore()
  const { state: bootState } = storeToRefs(bootstrapStore)
  const noticeStore = useNoticeStore()
  // Phase 14 修缮:用 router 实例直接 import(替代 useRouter()),
  // 避免 setup store 顶层调 composable 在某些初始化时机下拿不到 router context

  const datasources = computed(() => (bootState.value?.datasources || []) as any[])
  const mysqlDatasources = computed(() =>
    datasources.value.filter((ds: any) => String(ds.db_type || '').toLowerCase().includes('mysql')),
  )
  // Phase 14 #3 — /sql-diagnosis 支持 MySQL / DM / Oracle 三方言。
  // scenario-lab / schema-import 仍只用 mysqlDatasources(materialize 流程
  // 当前只实现 MySQL 方言)。
  const diagnosableDatasources = computed(() =>
    datasources.value.filter((ds: any) => {
      const t = String(ds.db_type || '').toLowerCase()
      return t === 'mysql' || t === 'dm' || t === 'oracle'
    }),
  )
  // Phase 14 #1 合规防御 — 选中 datasource 的 environment
  const selectedDs = computed(() =>
    datasources.value.find((ds: any) => ds.id === datasourceId.value),
  )
  const selectedDsEnvironment = computed<string>(() =>
    (selectedDs.value?.environment as string) || 'sandbox',
  )
  const sandboxWriteLocked = computed(() =>
    !!datasourceId.value && selectedDsEnvironment.value !== 'sandbox',
  )
  const validScenarios = computed(() => items.value.filter((it) => !it.error))
  const brokenScenarios = computed(() => items.value.filter((it) => !!it.error))

  // 当前 step 启发推断
  const currentStep = computed<StepId>(() => {
    if (!selectedId.value) return 'schema'
    if (!materializeResult.value) return 'data'
    if (!Object.values(slowSqlResults.value).length) return 'sql'
    return 'verify'
  })

  // ─── helpers ───────────────────────────────────────────────────────────
  function isSelected(id?: string): boolean {
    return !!id && id === selectedId.value
  }

  function renderSql(sql: string | undefined): { text: string; missing: string[] } {
    if (!sql) return { text: '', missing: [] }
    const vars = detail.value?.variables || {}
    const missing = new Set<string>()
    const text = sql.replace(TEMPLATE_VAR_RE, (full, name) => {
      if (Object.prototype.hasOwnProperty.call(vars, name)) {
        const v = (vars as any)[name]
        return v === null || v === undefined ? '' : String(v)
      }
      missing.add(name)
      return full
    })
    return { text, missing: Array.from(missing).sort() }
  }

  function planColumns(plan: Record<string, unknown>[]): string[] {
    if (!plan.length) return []
    return Object.keys(plan[0])
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

  function isPlanDiffImproved(d: PlanDiffResult): boolean {
    return d.diff.rows_delta.change < 0 || d.diff.issues_resolved.length > 0
  }
  function isPlanDiffRegressed(d: PlanDiffResult): boolean {
    return d.diff.rows_delta.change > 0 || d.diff.issues_introduced.length > 0
  }

  function toggleSlowSqlExpansion(idx: number): void {
    slowSqlExpanded.value = { ...slowSqlExpanded.value, [idx]: !slowSqlExpanded.value[idx] }
  }

  function gotoTask(taskId: string): void {
    router.push({ path: '/data-compare', query: { task_id: taskId } })
  }

  function gotoHistory(): void {
    router.push({ path: '/history', query: { type: 'lineage' } })
  }

  // ─── actions ───────────────────────────────────────────────────────────
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
        noticeStore.setNotice(`一键链完成但有问题:${result.error || '查看下方 verify 结果'}`)
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
      const verified = await apiJson<VerifyResult>(
        `/api/scenarios/${selectedId.value}/verify`
        + (projectId.value ? `?project_id=${encodeURIComponent(projectId.value)}` : ''),
        'GET',
      )
      verifyResult.value = verified
      const s = verified.summary
      noticeStore.setNotice(`回归校验：${s.pass} pass · ${s.fail} fail · ${s.skipped} skipped`)
    } catch (e) {
      noticeStore.setNotice(`Verify 失败：${noticeStore.toErrorMessage(e)}`)
    } finally {
      verifying.value = false
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
      const recorded = await apiJson<RecordResult>(
        `/api/scenarios/${selectedId.value}/record`,
        'POST',
        { datasource_id: datasourceId.value, project_id: projectId.value },
      )
      recordResult.value = recorded
      noticeStore.setNotice(`✨ 已创建 ${recorded.tasks.length} 个对比任务`)
    } catch (e) {
      lastError.value = noticeStore.toErrorMessage(e)
      noticeStore.setNotice(`Record 失败：${lastError.value}`)
    } finally {
      recording.value = false
    }
  }

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
        scenario_id: selectedId.value,
        workload_name: workload.name || '',
      })
      slowSqlResults.value = { ...slowSqlResults.value, [idx]: result }
      slowSqlExpanded.value = { ...slowSqlExpanded.value, [idx]: true }
    } catch (e) {
      let msg = noticeStore.toErrorMessage(e)
      // 把"表不存在"翻译成"先一键全套落库" — slow_query analyze 跑 EXPLAIN 在真表上,
      // 用户常在没 materialize 前先点 🔬 分析,后端 1146 错误对新手不友好
      if (/doesn't exist|不存在|1146/i.test(msg)) {
        msg = '⚠ 表还没建,请先点上方 🚀 一键全套 把 yml 的表落到数据库,再点 🔬 分析。\n原始错误: ' + msg
      }
      slowSqlErrors.value = { ...slowSqlErrors.value, [idx]: msg }
      slowSqlExpanded.value = { ...slowSqlExpanded.value, [idx]: true }
    } finally {
      slowSqlAnalyzing.value = { ...slowSqlAnalyzing.value, [idx]: false }
    }
  }

  async function runPlanDiff(idx: number): Promise<void> {
    const cur = slowSqlResults.value[idx]
    if (!cur?.history_id || !cur.sql_hash || !datasourceId.value) {
      planDiffErrors.value = { ...planDiffErrors.value, [idx]: '需要先跑过分析(拿到 history_id)' }
      return
    }
    planDiffLoading.value = { ...planDiffLoading.value, [idx]: true }
    planDiffErrors.value = { ...planDiffErrors.value, [idx]: '' }
    try {
      const hist = await apiJson<{ items: PlanHistoryItem[] }>(
        `/api/slow-sql/plan-history?datasource_id=${encodeURIComponent(datasourceId.value)}`
        + `&sql_hash=${encodeURIComponent(cur.sql_hash)}&limit=2`,
        'GET',
      )
      if (!hist.items || hist.items.length < 2) {
        planDiffErrors.value = { ...planDiffErrors.value, [idx]:
          '同 SQL 没有更早的历史可对比(改写 SQL/加索引后重跑就能 diff 了)' }
        return
      }
      const [newest, prev] = hist.items
      const diff = await apiJson<PlanDiffResult>(
        `/api/slow-sql/plan-diff?plan_a_id=${prev.id}&plan_b_id=${newest.id}`,
        'GET',
      )
      planDiffs.value = { ...planDiffs.value, [idx]: diff }
    } catch (e) {
      planDiffErrors.value = { ...planDiffErrors.value, [idx]: noticeStore.toErrorMessage(e) }
    } finally {
      planDiffLoading.value = { ...planDiffLoading.value, [idx]: false }
    }
  }

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
        dialect: analysisResult.dialect || 'mysql',
      })
      enrichResults.value = { ...enrichResults.value, [idx]: result }
      if (!result.ok) {
        noticeStore.setNotice(result.error || 'AI 复核未启用')
      } else {
        const pct = result.expected_coverage.coverage_pct
        noticeStore.setNotice(
          result.expected_coverage.missing.length
            ? `✨ AI 复核完成，覆盖率 ${pct}%`
            : `✨ AI 复核完成`,
        )
      }
    } catch (e) {
      noticeStore.setNotice(`AI 复核失败：${noticeStore.toErrorMessage(e)}`)
    } finally {
      enrichLoading.value = { ...enrichLoading.value, [idx]: false }
    }
  }

  // ─── import from datasource(P1-1 UI 入口)──────────────────────────────
  function openImportDialog(): void {
    importForm.value = {
      datasource_id: datasourceId.value || '',
      table_names: '', scenario_id: '', scenario_name: '',
      default_rows: 1000, save: true,
    }
    importResult.value = null
    importError.value = ''
    importDialogOpen.value = true
  }

  async function submitImport(): Promise<void> {
    if (!importForm.value.datasource_id || !importForm.value.table_names.trim()
        || !importForm.value.scenario_id.trim()) {
      importError.value = '需要填 datasource / table_names / scenario_id'
      return
    }
    importing.value = true
    importError.value = ''
    try {
      const tables = importForm.value.table_names.split(',').map(s => s.trim()).filter(Boolean)
      const r = await apiJson<ImportFromDsResult>(
        '/api/scenarios/import-from-datasource', 'POST', {
          datasource_id: importForm.value.datasource_id,
          table_names: tables,
          scenario_id: importForm.value.scenario_id,
          scenario_name: importForm.value.scenario_name,
          default_rows: importForm.value.default_rows,
          save: importForm.value.save,
        },
      )
      importResult.value = r
      if (r.saved_path) {
        noticeStore.setNotice(`✨ 已保存到 config/scenarios/${r.saved_path}`)
        await loadList()
      }
    } catch (e) {
      importError.value = noticeStore.toErrorMessage(e)
    } finally {
      importing.value = false
    }
  }

  function copyImportYml(): void {
    if (!importResult.value?.yml_text) return
    navigator.clipboard?.writeText(importResult.value.yml_text)
    noticeStore.setNotice('✓ yml 已复制')
  }

  // ─── Phase 14 修缮:Quick mode actions ──────────────────────────────────
  async function runQuickAnalyze(): Promise<void> {
    if (!quickDatasourceId.value || !quickSql.value.trim()) {
      quickError.value = '需要先选 datasource + 粘 SQL'
      return
    }
    quickAnalyzing.value = true
    quickError.value = ''
    quickResult.value = null
    quickPlanDiff.value = null
    try {
      // Phase 14 #3 — 先跑 preflight 静态检查,blocking=true 拦
      try {
        const preflight = await apiJson<{
          risk_level: string
          blocking: boolean
          rules: Array<{ code: string; level: string; message: string }>
        }>('/api/sql-diagnosis/preflight', 'POST', {
          sql: quickSql.value,
          datasource_id: quickDatasourceId.value,
        })
        if (preflight.blocking) {
          const blockRules = preflight.rules
            .filter((r) => r.level === 'block' || r.level === 'high')
            .map((r) => `[${r.code}] ${r.message}`)
            .join('\n')
          quickError.value = '❌ preflight 静态检查未通过(阻塞):\n' + blockRules
          return
        }
        // medium 级别给 confirm,low 直接放行
        if (preflight.risk_level === 'medium' && preflight.rules.length) {
          const warnText = preflight.rules
            .map((r) => `[${r.code}] ${r.message}`)
            .join('\n')
          const ok = confirm(
            '⚠ preflight 发现 ' + preflight.rules.length + ' 个风险提示(不阻塞):\n\n'
            + warnText + '\n\n是否继续 EXPLAIN?',
          )
          if (!ok) return
        }
      } catch (e) {
        // preflight 失败 → 仍允许 analyze,只警告
        console.warn('preflight 调用失败,继续 analyze:', e)
      }

      // Phase 14 #3 — 任何环境的 EXPLAIN 都走 OperationPreviewModal(/sql-diagnosis
      // view 在 onMounted 时注入 confirmAnalyzePromise);如果 view 没注入(单元
      // 测试 / 旧路径)就 fallback 到 confirm()。prod/staging 必须走 modal;
      // sandbox 也走(用户期望"任何环境都给清晰的影响声明")
      const ds = (diagnosableDatasources.value as any[]).find((d) => d.id === quickDatasourceId.value)
      const env = ds?.environment || 'unknown'
      if (confirmAnalyzePromise.value) {
        const ok = await confirmAnalyzePromise.value()
        if (!ok) return
      } else if (env === 'prod' || env === 'staging') {
        // fallback:没注入 modal callback,退化为 confirm 文案(测试 / 非
        // /sql-diagnosis 入口走这条)
        const dbType = String(ds?.db_type || '').toLowerCase()
        let msg = ''
        if (dbType === 'oracle') {
          msg = `即将在 ${env} 环境的 Oracle 数据源上执行 EXPLAIN PLAN FOR,会向诊断表 PLAN_TABLE 写一行临时记录(非业务表)。\n\n本操作不会修改业务数据,但会被记录审计。\n\n是否继续?`
        } else if (dbType === 'dm') {
          msg = `即将在 ${env} 环境的 DM 数据源上执行 EXPLAIN SELECT。\n\n本操作不会修改业务数据,但会消耗优化器资源,并记录审计。\n\n是否继续?`
        } else {
          msg = `即将在 ${env} 环境的 ${dbType} 数据源上执行 EXPLAIN SELECT。\n\n本操作不会修改业务数据,但会记录审计。\n\n是否继续?`
        }
        if (!confirm(msg)) return
      }

      const result = await apiJson<SlowSqlResult>('/api/slow-sql/analyze', 'POST', {
        sql: quickSql.value,
        datasource_id: quickDatasourceId.value,
        scenario_id: quickTagScenarioId.value || '',
        workload_name: '',
        save_history: true,
      })
      quickResult.value = result
      // 同步刷一次 history 列表给 UI 展示
      await refreshQuickHistory()
      noticeStore.setNotice(`✓ EXPLAIN 完成,${result.issues.length} 个 issue / ${result.suggestions.length} 个建议`)
    } catch (e) {
      quickError.value = noticeStore.toErrorMessage(e)
    } finally {
      quickAnalyzing.value = false
    }
  }

  async function refreshQuickHistory(): Promise<void> {
    const hash = quickResult.value?.sql_hash
    if (!hash || !quickDatasourceId.value) return
    try {
      const r = await apiJson<{ items: PlanHistoryItem[] }>(
        `/api/slow-sql/plan-history?datasource_id=${encodeURIComponent(quickDatasourceId.value)}`
        + `&sql_hash=${encodeURIComponent(hash)}&limit=20`,
        'GET',
      )
      quickPlanHistory.value = r.items || []
    } catch {
      quickPlanHistory.value = []
    }
  }

  async function runQuickEnrich(): Promise<void> {
    if (!quickResult.value) {
      quickError.value = '请先运行规则分析'
      return
    }
    quickEnriching.value = true
    try {
      const result = await apiJson<SlowSqlEnrichResult>('/api/slow-sql/enrich', 'POST', {
        sql: quickSql.value,
        plan: quickResult.value.plan,
        issues: quickResult.value.issues,
        suggestions: quickResult.value.suggestions,
        expected_optimizations: [],  // quick mode 无 yml expected
        dialect: quickResult.value.dialect || 'mysql',
      })
      quickEnrichResult.value = result
      if (!result.ok) {
        noticeStore.setNotice(result.error || 'AI 复核未启用')
      } else {
        noticeStore.setNotice('✨ AI 复核完成')
      }
    } catch (e) {
      noticeStore.setNotice(`AI 复核失败:${noticeStore.toErrorMessage(e)}`)
    } finally {
      quickEnriching.value = false
    }
  }

  async function runQuickPlanDiff(): Promise<void> {
    if (!quickResult.value?.history_id || !quickResult.value.sql_hash || !quickDatasourceId.value) {
      quickPlanDiffError.value = '需要先跑过分析(拿到 history_id)'
      return
    }
    quickPlanDiffLoading.value = true
    quickPlanDiffError.value = ''
    try {
      const hist = await apiJson<{ items: PlanHistoryItem[] }>(
        `/api/slow-sql/plan-history?datasource_id=${encodeURIComponent(quickDatasourceId.value)}`
        + `&sql_hash=${encodeURIComponent(quickResult.value.sql_hash)}&limit=2`,
        'GET',
      )
      if (!hist.items || hist.items.length < 2) {
        quickPlanDiffError.value = '同 SQL 没有更早的历史可对比(改写 SQL/加索引后重跑就能 diff 了)'
        return
      }
      const [newest, prev] = hist.items
      const diff = await apiJson<PlanDiffResult>(
        `/api/slow-sql/plan-diff?plan_a_id=${prev.id}&plan_b_id=${newest.id}`,
        'GET',
      )
      quickPlanDiff.value = diff
    } catch (e) {
      quickPlanDiffError.value = noticeStore.toErrorMessage(e)
    } finally {
      quickPlanDiffLoading.value = false
    }
  }

  function clearQuickAnalysis(): void {
    quickResult.value = null
    quickEnrichResult.value = null
    quickPlanDiff.value = null
    quickError.value = ''
    quickPlanDiffError.value = ''
  }

  return {
    // state
    items, loadingList, selectedId, detail, detailPath, loadingDetail,
    datasourceId, dropFirst, aiFill, projectId,
    materializing, recording, verifying, runningAll,
    materializeResult, recordResult, verifyResult, runAllResult, lastError,
    slowSqlResults, slowSqlAnalyzing, slowSqlExpanded, slowSqlErrors,
    planDiffs, planDiffLoading, planDiffErrors,
    enrichResults, enrichLoading,
    importDialogOpen, importing, importResult, importError, importForm,
    viewMode, quickSql, quickDatasourceId, quickTagScenarioId,
    quickAnalyzing, quickEnriching, quickPlanDiffLoading,
    quickResult, quickEnrichResult, quickPlanDiff, quickPlanHistory,
    quickError, quickPlanDiffError,
    confirmAnalyzePromise,
    // derived
    datasources, mysqlDatasources, diagnosableDatasources,
    validScenarios, brokenScenarios, currentStep,
    selectedDs, selectedDsEnvironment, sandboxWriteLocked,
    // helpers
    isSelected, renderSql, planColumns, statusBadgeClass, statusLabel,
    verdictBadgeClass, confidenceBadgeClass, anomalyLabel, totalRows,
    isPlanDiffImproved, isPlanDiffRegressed, toggleSlowSqlExpansion,
    gotoTask, gotoHistory,
    // actions
    loadList, selectScenario, runMaterialize, runAll, runVerify, runRecord,
    runSlowSqlAnalysis, runPlanDiff, runAiEnrich,
    openImportDialog, submitImport, copyImportYml,
    runQuickAnalyze, runQuickEnrich, runQuickPlanDiff, refreshQuickHistory, clearQuickAnalysis,
  }
})
