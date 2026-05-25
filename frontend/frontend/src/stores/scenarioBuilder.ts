/**
 * Phase 14 #3 Round 4 — 可视化 Scenario Builder store
 *
 * 表单状态 + 实时 yml 预览生成。后端 POST /api/scenarios/save-yml 接收
 * dict 形式的 scenario,Pydantic 校验后落盘。
 *
 * 设计:不复用 sandbox.ts(builder 完全独立的表单流),自管状态。
 */
import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import { apiJson } from '../api'
import { useNoticeStore } from './notice'

// ─── DSL 类型(对齐后端 app/scenarios/models.py) ───────────────────────────

export type GeneratorKind =
  | 'sequence' | 'uuid_short' | 'random_int' | 'realistic'
  | 'enum' | 'timestamp' | 'constant'
  | 'foreign_key'

export type FKDistribution = 'uniform' | 'zipf'

export type DistributionKind = 'lognormal' | 'normal' | 'uniform' | 'exponential'

export interface DistParamsForm {
  kind: DistributionKind
  mu?: number
  sigma?: number
  min?: number
  max?: number
  lambda?: number   // exponential 用
}

export interface ColumnForm {
  name: string
  type: string
  pk: boolean
  nullable: boolean
  gen: GeneratorKind
  // per-gen knobs(根据 gen 显示对应字段)
  values_text: string         // enum/realistic/constant: 逗号分隔
  distribution_text: string   // enum: 逗号分隔权重
  range_min: number | ''
  range_max: number | ''
  zipf: number | ''           // random_int 偏斜
  ts_start: string            // timestamp YYYY-MM-DD
  ts_end: string
  prefix: string              // sequence 前缀
  dist_params_enabled: boolean
  dist_params: DistParamsForm
  // Round 6 FK 字段(仅 gen=foreign_key 时有效)
  fk_ref_table: string        // 引用表名(来自 form.tables[].name)
  fk_ref_column: string       // 引用列名(来自所选表的 columns)
  fk_match_rate: number       // 0.0 ~ 1.0
  fk_unique: boolean
  fk_distribution: FKDistribution
  fk_zipf_alpha: number
  // Round 6 N 阶段 — 金融行业 faker provider
  faker_provider: string      // chinese_id / mobile_phone / fund_acc_no / ... 空 = 不用
}

export interface IndexForm {
  columns_text: string  // 逗号分隔
  unique: boolean
}

export interface TableForm {
  name: string
  role: 'source' | 'target' | 'intermediate' | 'reference'
  rows: number
  columns: ColumnForm[]
  indexes: IndexForm[]
}

export interface VariableForm {
  name: string
  value: string
}

// Round 6 H.4 — Anomaly + Workload 表单
export type AnomalyKind =
  | 'missing_rows' | 'extra_rows' | 'value_drift'
  | 'null_drift' | 'duplicate_pk' | 'type_mismatch'

export interface AnomalyForm {
  kind: AnomalyKind
  table: string                // 引用 form.tables[].name
  column: string               // value_drift / null_drift / type_mismatch 需要
  count_mode: 'count' | 'fraction'   // 哪种数量模式
  count: number                // 精确数量
  fraction: number             // 0.0~1.0 比例
  perturbation: string         // value_drift 用,如 "±5%" / "0.05"
}

export type WorkloadKind = 'slow_query' | 'compare_task' | 'lineage_script'

export interface WorkloadForm {
  kind: WorkloadKind
  name: string
  sql: string                  // slow_query / lineage_script / compare_task source 用
  // compare_task 专属
  target_sql: string           // 空 = 用 sql(同表对比)
  key_columns: string          // 逗号分隔
  expected_only_source: number
  expected_only_target: number
  expected_diff: number
  expected_same: number
  // slow_query 专属
  intentional_issues: string[]
  expected_optimizations: string[]
}

export interface ScenarioBuilderForm {
  id: string
  name: string
  description: string
  dialect: 'mysql' | 'dm' | 'oracle' | 'db2'
  seed: number
  variables: VariableForm[]
  tables: TableForm[]
  anomalies: AnomalyForm[]
  workloads: WorkloadForm[]
}


// ─── factories(给 + 添加 按钮用) ──────────────────────────────────────────

export function makeEmptyColumn(): ColumnForm {
  return {
    name: '', type: 'VARCHAR(100)', pk: false, nullable: true,
    gen: 'realistic',
    values_text: '', distribution_text: '',
    range_min: '', range_max: '',
    zipf: '',
    ts_start: '', ts_end: '',
    prefix: '',
    dist_params_enabled: false,
    dist_params: { kind: 'lognormal', mu: 0, sigma: 1, min: 0 },
    fk_ref_table: '',
    fk_ref_column: '',
    fk_match_rate: 1.0,
    fk_unique: false,
    fk_distribution: 'uniform',
    fk_zipf_alpha: 1.2,
    faker_provider: '',
  }
}

export function makeEmptyTable(): TableForm {
  return {
    name: '', role: 'source', rows: 1000,
    columns: [makeEmptyColumn()],
    indexes: [],
  }
}

export function makeEmptyIndex(): IndexForm {
  return { columns_text: '', unique: false }
}

export function makeEmptyAnomaly(): AnomalyForm {
  return {
    kind: 'missing_rows',
    table: '',
    column: '',
    count_mode: 'fraction',
    count: 100,
    fraction: 0.05,
    perturbation: '±5%',
  }
}

export function makeEmptyWorkload(): WorkloadForm {
  return {
    kind: 'slow_query',
    name: '',
    sql: '',
    target_sql: '',
    key_columns: '',
    expected_only_source: 0,
    expected_only_target: 0,
    expected_diff: 0,
    expected_same: 0,
    intentional_issues: [],
    expected_optimizations: [],
  }
}


// ─── store ────────────────────────────────────────────────────────────────

export const useScenarioBuilderStore = defineStore('scenarioBuilder', () => {
  const form = reactive<ScenarioBuilderForm>({
    id: '',
    name: '',
    description: '',
    dialect: 'mysql',
    seed: 42,
    variables: [],
    tables: [makeEmptyTable()],
    anomalies: [],
    workloads: [],
  })

  const saving = ref(false)
  const saveError = ref('')
  const saveResult = ref<{ scenario_id: string; saved_path: string } | null>(null)

  // 当前 step(壳子用 1-4 步;后续可加路由 hash)
  const currentStep = ref<'meta' | 'tables' | 'workloads' | 'preview'>('meta')

  // ─── form → dict (post 给后端 / yml 预览用) ─────────────────────────────

  function toScenarioDict(): Record<string, any> {
    const out: Record<string, any> = {
      id: form.id.trim(),
      name: form.name.trim() || form.id.trim(),
      dialect: form.dialect,
      seed: form.seed,
    }
    if (form.description.trim()) out.description = form.description.trim()
    if (form.variables.length) {
      out.variables = Object.fromEntries(
        form.variables
          .filter((v) => v.name.trim())
          .map((v) => [v.name.trim(), v.value]),
      )
    }
    out.tables = form.tables.map((t) => tableToDict(t))
    if (form.anomalies.length) {
      out.anomalies = form.anomalies.map((a) => anomalyToDict(a))
    }
    if (form.workloads.length) {
      out.workloads = form.workloads.map((w) => workloadToDict(w))
    }
    return out
  }

  function anomalyToDict(a: AnomalyForm): Record<string, any> {
    const d: Record<string, any> = { kind: a.kind, table: a.table.trim() }
    // count vs fraction:精确优先
    if (a.count_mode === 'count') {
      d.count = Number(a.count) || 0
    } else {
      d.fraction = Number(a.fraction) || 0
    }
    // kind 专属字段
    if (['value_drift', 'null_drift', 'type_mismatch'].includes(a.kind) && a.column) {
      d.column = a.column.trim()
    }
    if (a.kind === 'value_drift' && a.perturbation) {
      d.perturbation = a.perturbation
    }
    return d
  }

  function workloadToDict(w: WorkloadForm): Record<string, any> {
    const d: Record<string, any> = { kind: w.kind, name: w.name.trim() }
    if (w.kind === 'slow_query') {
      if (w.sql.trim()) d.sql = w.sql
      if (w.intentional_issues.length) d.intentional_issues = w.intentional_issues
      if (w.expected_optimizations.length) d.expected_optimizations = w.expected_optimizations
    } else if (w.kind === 'compare_task') {
      if (w.sql.trim()) d.source_sql = w.sql
      if (w.target_sql.trim()) d.target_sql = w.target_sql
      const keys = w.key_columns.split(/[,\s]+/).map((k) => k.trim()).filter(Boolean)
      if (keys.length) d.key_columns = keys
      // expected 对比块
      const exp: Record<string, number> = {}
      if (w.expected_only_source) exp.only_source = w.expected_only_source
      if (w.expected_only_target) exp.only_target = w.expected_only_target
      if (w.expected_diff) exp.diff = w.expected_diff
      if (w.expected_same) exp.same = w.expected_same
      if (Object.keys(exp).length) d.expected = exp
    } else if (w.kind === 'lineage_script') {
      if (w.sql.trim()) d.sql = w.sql
    }
    return d
  }

  function tableToDict(t: TableForm): Record<string, any> {
    return {
      name: t.name.trim(),
      role: t.role,
      rows: Number(t.rows) || 0,
      columns: t.columns.map((c) => columnToDict(c)),
      ...(t.indexes.length ? { indexes: t.indexes.map((i) => indexToDict(i)) } : {}),
    }
  }

  function columnToDict(c: ColumnForm): Record<string, any> {
    const d: Record<string, any> = {
      name: c.name.trim(),
      type: c.type.trim(),
      gen: c.gen,
    }
    if (c.pk) d.pk = true
    if (!c.nullable) d.nullable = false

    const values = splitCsv(c.values_text)
    const dist = splitCsv(c.distribution_text)
      .map((x) => Number(x))
      .filter((x) => !Number.isNaN(x))

    // per-gen knob(模仿 yml 字段命名)
    switch (c.gen) {
      case 'sequence':
        if (c.prefix.trim()) d.values = [c.prefix.trim()]
        break
      case 'uuid_short':
        break
      case 'random_int':
        if (c.range_min !== '' || c.range_max !== '') {
          d.range = [Number(c.range_min) || 0, Number(c.range_max) || 100]
        }
        if (c.zipf !== '') d.zipf = Number(c.zipf)
        break
      case 'realistic':
        if (values.length) d.values = values
        if (c.dist_params_enabled) {
          d.dist_params = serializeDistParams(c.dist_params)
        }
        break
      case 'enum':
        if (values.length) d.values = values
        if (dist.length && dist.length === values.length) d.distribution = dist
        break
      case 'timestamp':
        if (c.ts_start || c.ts_end) {
          d.range = [c.ts_start || '2025-01-01', c.ts_end || '2026-12-31']
        }
        break
      case 'constant':
        if (values.length) d.values = values
        break
      case 'foreign_key':
        if (c.fk_ref_table && c.fk_ref_column) {
          d.references = `${c.fk_ref_table}.${c.fk_ref_column}`
        }
        if (c.fk_match_rate !== 1.0) d.match_rate = c.fk_match_rate
        if (c.fk_unique) d.fk_unique = true
        if (c.fk_distribution !== 'uniform') {
          d.fk_distribution = c.fk_distribution
          if (c.fk_distribution === 'zipf' && c.fk_zipf_alpha !== 1.2) {
            d.fk_zipf_alpha = c.fk_zipf_alpha
          }
        }
        break
    }
    // Round 6 N — faker_provider 可叠加在 realistic 上(也兼容其它 gen)
    if (c.faker_provider) d.faker_provider = c.faker_provider
    return d
  }

  function indexToDict(i: IndexForm): Record<string, any> {
    const cols = splitCsv(i.columns_text)
    return { columns: cols, ...(i.unique ? { unique: true } : {}) }
  }

  // ─── yml 预览(基于 toScenarioDict 拿干净 dict 再 yaml dump) ────────────
  // 前端没引 js-yaml,自己做最小 dump(scenario 结构有限,够用)

  const ymlPreview = computed(() => dumpYaml(toScenarioDict()))

  // ─── save ───────────────────────────────────────────────────────────────

  // Round 6 L — 从内置模板加载
  const templatesList = ref<Array<{ file: string; id: string; name: string; description: string; tables_count: number; dialect: string }>>([])
  const loadingTemplates = ref(false)

  async function loadTemplatesList() {
    loadingTemplates.value = true
    try {
      const r = await apiJson<{ items: any[] }>('/api/scenarios/templates', 'GET')
      templatesList.value = r.items.filter((i: any) => !i.error)
    } finally {
      loadingTemplates.value = false
    }
  }

  // Round 6 M — 从 metadata csv 导入表/列(给 form 填充)
  const importingMetadata = ref(false)
  const metadataError = ref('')

  async function importFromMetadataCsv(csvText: string): Promise<boolean> {
    metadataError.value = ''
    importingMetadata.value = true
    try {
      const r = await apiJson<{ tables: any[]; warnings: string[]; tables_count: number }>(
        '/api/scenarios/import-from-metadata', 'POST', { csv_text: csvText },
      )
      // 把返回的 tables 转成 TableForm 追加
      const newTables: TableForm[] = r.tables.map((t: any) => ({
        name: t.name,
        role: 'source',
        rows: t.rows || 1000,
        columns: (t.columns || []).map((c: any): ColumnForm => {
          const col = makeEmptyColumn()
          col.name = c.name || ''
          col.type = 'VARCHAR(100)'
          col.gen = c.gen === 'enum' ? 'enum' : 'realistic'
          if (c.values && c.values.length) col.values_text = c.values.join(', ')
          if (c.freq && c.freq.length) col.distribution_text = c.freq.join(', ')
          return col
        }),
        indexes: [],
      }))
      // 替换 form.tables(覆盖)
      form.tables = newTables.length ? newTables : [makeEmptyTable()]
      return true
    } catch (e: any) {
      metadataError.value = useNoticeStore().toErrorMessage(e)
      return false
    } finally {
      importingMetadata.value = false
    }
  }

  async function loadFromTemplate(templateFile: string) {
    const noticeStore = useNoticeStore()
    try {
      const r = await apiJson<{ scenario: any }>(`/api/scenarios/templates/${templateFile}`, 'GET')
      const sc = r.scenario
      // 把 backend dict 翻成 form state
      form.id = (sc.id || '') + '-copy'  // 防覆盖原 template
      form.name = sc.name || ''
      form.description = sc.description || ''
      form.dialect = sc.dialect || 'mysql'
      form.seed = sc.seed || 42
      form.variables = Object.entries(sc.variables || {}).map(([name, value]) => ({
        name, value: String(value),
      }))
      form.tables = (sc.tables || []).map((t: any) => tableDictToForm(t))
      form.anomalies = (sc.anomalies || []).map((a: any) => anomalyDictToForm(a))
      form.workloads = (sc.workloads || []).map((w: any) => workloadDictToForm(w))
      noticeStore.setNotice(`✓ 已加载模板 ${sc.id};请改 id 后保存`)
    } catch (e: any) {
      noticeStore.setNotice('加载模板失败: ' + noticeStore.toErrorMessage(e))
    }
  }

  async function save(overwrite = false): Promise<boolean> {
    const noticeStore = useNoticeStore()
    saveError.value = ''
    if (!form.id.match(/^[A-Za-z0-9_\-]+$/)) {
      saveError.value = 'scenario id 只允许字母 / 数字 / _ / -,如 my-fixture-v1'
      return false
    }
    if (!form.tables.length || !form.tables[0].name.trim()) {
      saveError.value = '至少添加一张表 + 表名'
      return false
    }
    saving.value = true
    try {
      const body = { scenario: toScenarioDict(), overwrite }
      const r = await apiJson<{ scenario_id: string; saved_path: string }>(
        '/api/scenarios/save-yml', 'POST', body,
      )
      saveResult.value = r
      noticeStore.setNotice(`✓ 场景 ${r.scenario_id} 已保存为 config/scenarios/${r.saved_path}`)
      return true
    } catch (e: any) {
      saveError.value = noticeStore.toErrorMessage(e)
      return false
    } finally {
      saving.value = false
    }
  }

  // ─── helpers exposed ────────────────────────────────────────────────────

  function addTable() { form.tables.push(makeEmptyTable()) }
  function removeTable(idx: number) { form.tables.splice(idx, 1) }
  function clearTables() { form.tables = [makeEmptyTable()] }   // 重置成 1 张空表
  function resetForm() {
    form.id = ''
    form.name = ''
    form.description = ''
    form.dialect = 'mysql'
    form.seed = 42
    form.variables = []
    form.tables = [makeEmptyTable()]
    form.anomalies = []
    form.workloads = []
    saveError.value = ''
    saveResult.value = null
  }
  function addColumn(tableIdx: number) { form.tables[tableIdx].columns.push(makeEmptyColumn()) }

  // Phase 14 #3 Round 5 — 粘贴 DDL 批量添加列
  // 接受 CREATE TABLE 片段 / 单独的列定义行 / desc 输出。极简 parser:
  //   id BIGINT NOT NULL,
  //   data_dt VARCHAR(8),
  //   PRIMARY KEY (id)
  // → 3 个 column,id 标 pk + nullable=false
  function addColumnsFromDdl(tableIdx: number, ddl: string): { added: number; pk_count: number; index_count: number; fk_count: number; warnings: string[] } {
    const table = form.tables[tableIdx]
    const parsed = parseDdlColumns(ddl)
    parsed.columns.forEach((c) => {
      table.columns.push({
        ...makeEmptyColumn(),
        name: c.name,
        type: c.type,
        pk: c.pk,
        nullable: c.nullable,
        gen: pickDefaultGenForType(c.type),
      })
    })
    // 处理 PRIMARY KEY (col1, col2) 这种独立声明 — 给 columns 数组里同名的标 pk
    parsed.pkColumns.forEach((pkName) => {
      const col = table.columns.find((c) => c.name === pkName)
      if (col) col.pk = true
    })
    // 处理 KEY / INDEX 声明 — 直接转 IndexForm
    parsed.indexes.forEach((idx) => {
      table.indexes.push({
        columns_text: idx.columns.join(', '),
        unique: idx.unique,
      })
    })
    // Round 6 — 处理 FOREIGN KEY 声明 → 改对应列 gen=foreign_key + references
    parsed.foreignKeys.forEach((fk) => {
      const col = table.columns.find((c) => c.name === fk.column)
      if (col) {
        col.gen = 'foreign_key'
        col.fk_ref_table = fk.ref_table
        col.fk_ref_column = fk.ref_column
      }
    })
    return {
      added: parsed.columns.length,
      pk_count: parsed.pkColumns.length || parsed.columns.filter((c) => c.pk).length,
      index_count: parsed.indexes.length,
      fk_count: parsed.foreignKeys.length,
      warnings: parsed.warnings,
    }
  }

  function removeColumn(tableIdx: number, colIdx: number) {
    form.tables[tableIdx].columns.splice(colIdx, 1)
  }
  function addIndex(tableIdx: number) { form.tables[tableIdx].indexes.push(makeEmptyIndex()) }
  function removeIndex(tableIdx: number, idxIdx: number) {
    form.tables[tableIdx].indexes.splice(idxIdx, 1)
  }
  function addVariable() { form.variables.push({ name: '', value: '' }) }
  function removeVariable(idx: number) { form.variables.splice(idx, 1) }
  function addAnomaly() { form.anomalies.push(makeEmptyAnomaly()) }
  function removeAnomaly(idx: number) { form.anomalies.splice(idx, 1) }
  function addWorkload(kind: WorkloadKind = 'slow_query') {
    const w = makeEmptyWorkload()
    w.kind = kind
    form.workloads.push(w)
  }
  function removeWorkload(idx: number) { form.workloads.splice(idx, 1) }

  return {
    form, saving, saveError, saveResult, currentStep,
    ymlPreview,
    save, toScenarioDict,
    addTable, removeTable, clearTables, resetForm,
    addColumn, removeColumn, addColumnsFromDdl,
    addIndex, removeIndex,
    addVariable, removeVariable,
    addAnomaly, removeAnomaly,
    addWorkload, removeWorkload,
    templatesList, loadingTemplates,
    loadTemplatesList, loadFromTemplate,
    importingMetadata, metadataError, importFromMetadataCsv,
  }
})


// ─── DDL parser ───────────────────────────────────────────────────────────

interface ParsedColumn {
  name: string
  type: string
  pk: boolean
  nullable: boolean
}

interface ParsedIndex {
  columns: string[]
  unique: boolean
}

interface ParsedFK {
  column: string
  ref_table: string
  ref_column: string
}

interface DdlParseResult {
  columns: ParsedColumn[]
  pkColumns: string[]    // 来自 "PRIMARY KEY (col1, col2)" 独立声明
  indexes: ParsedIndex[]
  foreignKeys: ParsedFK[] // FOREIGN KEY (col) REFERENCES tbl(col)
  warnings: string[]
}

/**
 * 极简 SQL DDL 列解析器。支持:
 * - 整段 CREATE TABLE xxx ( ... );  — 自动剥外层
 * - 独立的列定义行(MySQL / Oracle / DM 通用语法)
 * - PRIMARY KEY (col1, col2) — 标已存在列 pk
 * - KEY / INDEX / UNIQUE KEY xxx (col1, col2) — 加 index
 * - 反引号 / 双引号 / 中括号包标识符
 * 不支持(给 warning):FOREIGN KEY / CHECK / 自定义 enum / DM 特殊语法
 */
function parseDdlColumns(rawText: string): DdlParseResult {
  const result: DdlParseResult = {
    columns: [], pkColumns: [], indexes: [], foreignKeys: [], warnings: [],
  }
  let text = rawText.trim()
  if (!text) return result

  // 1) 剥 CREATE TABLE xxx (...) 外壳
  const createMatch = text.match(/CREATE\s+TABLE[^(]*\(([\s\S]+)\)\s*[;\s]*$/i)
  if (createMatch) {
    text = createMatch[1]
  }

  // 2) 按"顶层"逗号分割(深度跟踪括号)
  const lines = splitTopLevel(text, ',')
    .map((l) => l.trim().replace(/[;\r]+$/, ''))
    .filter(Boolean)
    .filter((l) => !l.startsWith('--'))  // SQL 行注释
    .filter((l) => !l.startsWith('/*'))   // 块注释起始

  for (const line of lines) {
    if (!line) continue
    const upper = line.toUpperCase()

    // PRIMARY KEY (xxx) — 不算列
    const pkMatch = line.match(/^PRIMARY\s+KEY\s*\(([^)]+)\)/i)
    if (pkMatch) {
      result.pkColumns.push(...splitNames(pkMatch[1]))
      continue
    }
    // UNIQUE KEY / UNIQUE INDEX xxx (cols)
    const uniqMatch = line.match(/^UNIQUE\s+(?:KEY|INDEX)?\s*[^(]*\(([^)]+)\)/i)
    if (uniqMatch) {
      result.indexes.push({ columns: splitNames(uniqMatch[1]), unique: true })
      continue
    }
    // KEY / INDEX xxx (cols)
    const keyMatch = line.match(/^(?:KEY|INDEX)\s+[^(]*\(([^)]+)\)/i)
    if (keyMatch) {
      result.indexes.push({ columns: splitNames(keyMatch[1]), unique: false })
      continue
    }
    // FOREIGN KEY (col) REFERENCES table(col) — Round 6 识别
    const fkMatch = line.match(
      /^(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([`"\[]?[\w.]+[`"\]]?)\s*\(([^)]+)\)/i,
    )
    if (fkMatch) {
      const localCols = splitNames(fkMatch[1])
      const refTable = fkMatch[2].replace(/^[`"\[]|[`"\]]$/g, '')
      const refCols = splitNames(fkMatch[3])
      // 只支持单列 FK(多列复合 FK 较少,P2 再加)
      if (localCols.length === 1 && refCols.length === 1) {
        result.foreignKeys.push({
          column: localCols[0],
          ref_table: refTable,
          ref_column: refCols[0],
        })
      } else {
        result.warnings.push(`复合 FK 暂不支持: ${line.slice(0, 60)}…`)
      }
      continue
    }
    // 其它 CONSTRAINT / CHECK — 给 warning 跳过
    if (
      upper.startsWith('CONSTRAINT') || upper.startsWith('CHECK')
      || upper.startsWith('FULLTEXT')
    ) {
      result.warnings.push(`跳过不支持的约束: ${line.slice(0, 60)}…`)
      continue
    }

    // 正常列定义 — 第一个 token = 列名,第二个开始 = 类型(可含 ())
    const parsed = parseSingleColumn(line)
    if (parsed) {
      result.columns.push(parsed)
    } else {
      result.warnings.push(`无法解析: ${line.slice(0, 60)}…`)
    }
  }
  return result
}

function parseSingleColumn(line: string): ParsedColumn | null {
  // 提取列名 — 支持 `xxx` / "xxx" / [xxx] / 裸 ident
  const nameMatch = line.match(/^(?:`([^`]+)`|"([^"]+)"|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))/)
  if (!nameMatch) return null
  const name = nameMatch[1] || nameMatch[2] || nameMatch[3] || nameMatch[4]
  const rest = line.slice(nameMatch[0].length).trim()

  // 类型:第一个 token,可能含括号(VARCHAR(40) / DECIMAL(28, 8))
  const typeMatch = rest.match(/^([A-Za-z][A-Za-z0-9_]*(?:\s*\([^)]*\))?)/)
  if (!typeMatch) return null
  const type = typeMatch[1].replace(/\s+/g, '').toUpperCase()
  const tail = rest.slice(typeMatch[0].length).toUpperCase()

  return {
    name,
    type,
    pk: /\bPRIMARY\s+KEY\b/.test(tail),
    nullable: !/\bNOT\s+NULL\b/.test(tail),
  }
}

function splitNames(s: string): string[] {
  return s.split(',')
    .map((x) => x.trim().replace(/^[`"\[]|[`"\]]$/g, ''))
    .filter(Boolean)
}

function splitTopLevel(text: string, sep: string): string[] {
  const out: string[] = []
  let depth = 0
  let buf = ''
  for (const ch of text) {
    if (ch === '(') depth++
    else if (ch === ')') depth = Math.max(0, depth - 1)
    if (ch === sep && depth === 0) {
      out.push(buf)
      buf = ''
    } else {
      buf += ch
    }
  }
  if (buf.trim()) out.push(buf)
  return out
}

/** 按列类型选个聪明的默认 generator。VARCHAR/TEXT → realistic;
 *  BIGINT/INT/NUMBER → random_int;DATE/DATETIME/TIMESTAMP → timestamp;
 *  DECIMAL/FLOAT/DOUBLE → realistic(后端类型嗅探会按 (p,s) 收敛精度);
 *  其它默认 realistic。 */
function pickDefaultGenForType(type: string): GeneratorKind {
  const t = type.toUpperCase()
  if (/^(BIGINT|INT|INTEGER|SMALLINT|TINYINT|NUMBER|NUMERIC)\b/.test(t)) {
    return 'random_int'
  }
  if (/^(DATE|DATETIME|TIMESTAMP)\b/.test(t)) {
    return 'timestamp'
  }
  // VARCHAR / CHAR / TEXT / LOB / DECIMAL / FLOAT / DOUBLE / 兜底
  return 'realistic'
}


// ─── utils ────────────────────────────────────────────────────────────────

// Round 6 L — backend scenario dict → 前端 form state(模板加载用)
function tableDictToForm(t: any): TableForm {
  return {
    name: t.name || '',
    role: t.role || 'source',
    rows: t.rows || 1000,
    columns: (t.columns || []).map((c: any) => columnDictToForm(c)),
    indexes: (t.indexes || []).map((i: any) => ({
      columns_text: (i.columns || []).join(', '),
      unique: !!i.unique,
    })),
  }
}

function anomalyDictToForm(a: any): AnomalyForm {
  const out = makeEmptyAnomaly()
  out.kind = a.kind || 'missing_rows'
  out.table = a.table || ''
  out.column = a.column || ''
  if (a.count !== undefined && a.count !== null) {
    out.count_mode = 'count'
    out.count = Number(a.count) || 0
  } else if (a.fraction !== undefined && a.fraction !== null) {
    out.count_mode = 'fraction'
    out.fraction = Number(a.fraction) || 0
  }
  if (a.perturbation !== undefined) {
    out.perturbation = String(a.perturbation)
  }
  return out
}

function workloadDictToForm(w: any): WorkloadForm {
  const out = makeEmptyWorkload()
  out.kind = w.kind || 'slow_query'
  out.name = w.name || ''
  if (out.kind === 'slow_query') {
    out.sql = w.sql || ''
    out.intentional_issues = Array.isArray(w.intentional_issues) ? w.intentional_issues : []
    out.expected_optimizations = Array.isArray(w.expected_optimizations) ? w.expected_optimizations : []
  } else if (out.kind === 'compare_task') {
    out.sql = w.source_sql || ''
    out.target_sql = w.target_sql || ''
    out.key_columns = Array.isArray(w.key_columns) ? w.key_columns.join(', ') : (w.key_columns || '')
    const exp = w.expected || {}
    out.expected_only_source = Number(exp.only_source) || 0
    out.expected_only_target = Number(exp.only_target) || 0
    out.expected_diff = Number(exp.diff) || 0
    out.expected_same = Number(exp.same) || 0
  } else if (out.kind === 'lineage_script') {
    out.sql = w.sql || ''
  }
  return out
}

function columnDictToForm(c: any): ColumnForm {
  const out = makeEmptyColumn()
  out.name = c.name || ''
  out.type = c.type || 'VARCHAR(100)'
  out.pk = !!c.pk
  out.nullable = c.nullable !== false
  out.gen = c.gen || 'realistic'
  if (c.values && Array.isArray(c.values)) out.values_text = c.values.join(', ')
  if (c.distribution && Array.isArray(c.distribution)) {
    out.distribution_text = c.distribution.join(', ')
  }
  if (c.range && Array.isArray(c.range)) {
    if (out.gen === 'random_int') {
      out.range_min = c.range[0] ?? ''
      out.range_max = c.range[1] ?? ''
    } else if (out.gen === 'timestamp') {
      out.ts_start = c.range[0] || ''
      out.ts_end = c.range[1] || ''
    }
  }
  if (c.zipf !== undefined) out.zipf = c.zipf
  if (out.gen === 'sequence' && c.values && c.values[0]) out.prefix = String(c.values[0])
  if (c.dist_params) {
    out.dist_params_enabled = true
    Object.assign(out.dist_params, c.dist_params)
  }
  // Round 6 FK
  if (c.references) {
    const refStr = String(c.references)
    const idx = refStr.lastIndexOf('.')
    if (idx > 0) {
      out.fk_ref_table = refStr.slice(0, idx)
      out.fk_ref_column = refStr.slice(idx + 1)
    }
  }
  if (c.match_rate !== undefined) out.fk_match_rate = c.match_rate
  if (c.fk_unique) out.fk_unique = true
  if (c.fk_distribution) out.fk_distribution = c.fk_distribution
  if (c.fk_zipf_alpha !== undefined) out.fk_zipf_alpha = c.fk_zipf_alpha
  if (c.faker_provider) out.faker_provider = c.faker_provider
  return out
}

function splitCsv(s: string): string[] {
  return s.split(/[,\n]/).map((x) => x.trim()).filter(Boolean)
}

function serializeDistParams(p: DistParamsForm): Record<string, any> {
  const out: Record<string, any> = { kind: p.kind }
  if (p.mu !== undefined) out.mu = p.mu
  if (p.sigma !== undefined) out.sigma = p.sigma
  if (p.min !== undefined) out.min = p.min
  if (p.max !== undefined) out.max = p.max
  if (p.kind === 'exponential' && p.lambda !== undefined) out.lambda = p.lambda
  return out
}

// 简易 yaml dump — scenario 结构已知,够用。不依赖 js-yaml lib。
function dumpYaml(obj: any, indent = 0): string {
  const pad = '  '.repeat(indent)
  if (obj === null || obj === undefined) return 'null'
  if (typeof obj === 'string') return needsQuote(obj) ? JSON.stringify(obj) : obj
  if (typeof obj === 'number' || typeof obj === 'boolean') return String(obj)
  if (Array.isArray(obj)) {
    if (!obj.length) return '[]'
    return obj.map((item) => {
      if (typeof item === 'object' && item !== null) {
        const inner = dumpYaml(item, indent + 1)
        return `${pad}- ${inner.trimStart()}`
      }
      return `${pad}- ${dumpYaml(item)}`
    }).join('\n')
  }
  if (typeof obj === 'object') {
    return Object.entries(obj).map(([k, v]) => {
      if (Array.isArray(v) || (typeof v === 'object' && v !== null)) {
        const isPrim = Array.isArray(v) && v.every((x) => typeof x !== 'object')
        if (isPrim && Array.isArray(v)) {
          return `${pad}${k}: [${(v as any[]).map((x) => dumpYaml(x)).join(', ')}]`
        }
        return `${pad}${k}:\n${dumpYaml(v, indent + 1)}`
      }
      return `${pad}${k}: ${dumpYaml(v)}`
    }).join('\n')
  }
  return String(obj)
}

function needsQuote(s: string): boolean {
  // 包含 yaml 元字符 / 数字开头 / 关键字 → 加引号防 yaml parser 误判
  if (!s) return true
  if (/^[\d-]/.test(s)) return true
  if (/[:#&*!?|<>%@`,\[\]\{\}]/.test(s)) return true
  if (/^(true|false|null|yes|no)$/i.test(s)) return true
  return false
}

// (H.4 起 anomaly + workload 走可视化编辑器,不再需要 yaml 文本 parser)
