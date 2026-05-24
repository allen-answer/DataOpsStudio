// SQL 优化沙盒共享类型(Phase 14 P2 完整版,view 拆分时抽出来)。
// 子组件 import 这里的 interface,store 也用。

export interface ScenarioListItem {
  id?: string
  name?: string
  path?: string
  tags?: string[]
  dialect?: string
  error?: string
}

export interface ScenarioListResponse {
  items: ScenarioListItem[]
}

export interface ColumnDef {
  name: string
  type: string
  pk?: boolean
  gen: string
}

export interface ColumnOverride {
  from: string
  rename?: string | null
  transform?: string | null
}

export interface IndexDef {
  columns: string[]
  unique?: boolean
  skip?: boolean
  reason?: string
}

export interface TableDef {
  name: string
  role: string
  rows: number
  columns: ColumnDef[]
  indexes: IndexDef[]
  derives_from?: string | null
  column_overrides?: ColumnOverride[]
  description?: string
}

export interface AnomalyDef {
  kind: string
  table: string
  column?: string | null
  fraction?: number | null
  count?: number | null
  [k: string]: unknown
}

export interface WorkloadDef {
  kind: string
  name?: string
  sql?: string
  intentional_issues?: string[]
  expected_optimizations?: string[]
  [k: string]: unknown
}

export interface SlowSqlIssue {
  severity: string
  code: string
  message: string
  table?: string
  detail?: string
}

export interface SlowSqlSuggestion {
  code: string
  message: string
  sql?: string
}

export interface SlowSqlSchemaContext {
  table: string
  schema: string
  table_row_count: number | null
  existing_indexes: Array<{ name: string; columns: string[]; unique: boolean; is_pk?: boolean }>
  where_columns: string[]
  join_columns: string[]
  uncovered_columns: string[]
  ddl_candidates: string[]
  rationale: string
}

export interface SlowSqlResult {
  dialect: string
  explain_sql: string
  plan: Record<string, unknown>[]
  issues: SlowSqlIssue[]
  suggestions: SlowSqlSuggestion[]
  schema_context?: SlowSqlSchemaContext[]
  history_id?: number | null
  sql_hash?: string
}

export interface PlanHistoryItem {
  id: number
  ts: string
  dialect: string
  sql_text: string
  sql_hash: string
  scenario_id?: string
  workload_name?: string
  plan?: Record<string, unknown>[]
  issues?: SlowSqlIssue[]
}

export interface PlanDiffResult {
  plan_a: { id: number; ts: string; sql_text: string }
  plan_b: { id: number; ts: string; sql_text: string }
  diff: {
    rows_delta: { a: number; b: number; change: number }
    type_changes: Array<{ idx: number; from: string; to: string }>
    extra_changes: Array<{ idx: number; removed: string[]; added: string[] }>
    issues_resolved: string[]
    issues_introduced: string[]
    summary: string
  }
}

export interface SlowSqlEnrichReview {
  code: string
  verdict: string
  rationale: string
}

export interface SlowSqlEnrichExtra {
  message: string
  sql?: string
  confidence?: string
}

export interface SlowSqlEnrichCoverage {
  matched: string[]
  missing: string[]
  coverage_pct: number
}

export interface SlowSqlEnrichResult {
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

export interface ScenarioDetail {
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

export interface ScenarioDetailResponse {
  scenario: ScenarioDetail
  path: string
}

export interface MaterializeTableResult {
  name: string
  schema?: string | null
  rows_inserted: number
  indexes_created?: number
}

export interface AiFillReport {
  ok: boolean
  calls: number
  filled_columns: string[]
  filled_distributions: string[]
  filled_descriptions: string[]
  errors: string[]
  skipped_reason: string
}

export interface MaterializeResult {
  dialect: string
  schemas_created: string[]
  tables: MaterializeTableResult[]
  warnings: string[]
  rows_generated?: Record<string, number>
  ai_fill?: AiFillReport
}

export interface RecordTask {
  id: string
  name: string
  source_id: string
  target_id: string
  source_sql: string
  target_sql: string
  key_columns: string[]
  project_id?: string
}

export interface RecordWarning {
  workload_name: string
  reason: string
}

export interface LineageRun {
  workload_name: string
  ok: boolean
  run_id: string
  error?: string
}

export interface RecordResult {
  tasks: RecordTask[]
  warnings: RecordWarning[]
  lineage_runs?: LineageRun[]
}

export interface VerifyItem {
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

export interface VerifyResult {
  scenario_id: string
  summary: { pass: number; fail: number; skipped: number }
  results: VerifyItem[]
}

export interface RunAllRunRecord {
  task_id: string
  task_name: string
  ok: boolean
  summary?: Record<string, number>
  error?: string
}

export interface RunAllResult {
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

export interface ImportFromDsResult {
  scenario_id: string
  saved_path: string | null
  yml_text: string
  tables_imported: number
  rows_per_table?: Record<string, number>
}

export type StepId = 'schema' | 'data' | 'sql' | 'verify'
