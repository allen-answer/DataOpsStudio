<script setup>
import { computed, onMounted, onUnmounted, provide, reactive, ref, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import { apiForm, apiGet, apiJson } from './api'
import BatchView from './views/BatchView.vue'
import DatasourceView from './views/DatasourceView.vue'
import HistoryView from './views/HistoryView.vue'
import LineageView from './views/LineageView.vue'
import WorkbenchView from './views/WorkbenchView.vue'
import WorkflowView from './views/WorkflowView.vue'
const views = [
  { id: 'datasource', label: '数据源管理' },
  { id: 'workbench', label: '数据对比任务工作台' },
  { id: 'workflow', label: '作业流' },
  { id: 'lineage', label: '单脚本血缘' },
  { id: 'batch', label: '多脚本分析' },
  { id: 'history', label: '执行历史' },
]

const activeView = ref('datasource')
const loading = ref(false)
const notice = ref('')
const noticeTimer = ref(null)
const asyncPollTimer = ref(null)
const selectedTaskId = ref('new')
const previewOutput = ref('')
const sourcePreviewData = ref(null)
const targetPreviewData = ref(null)
const sourceFields = ref([])
const targetFields = ref([])
const compareResult = ref(null)
const asyncJob = ref(null)
const asyncStatus = ref(null)
const selectedWorkflowId = ref('new')
const workflowResult = ref(null)
const workflowAsyncJob = ref(null)
const workflowAsyncStatus = ref(null)
const workflowAsyncPollTimer = ref(null)
const workflowRunHistory = ref([])    // list of run summaries for the selected workflow
const allWorkflowRuns = ref([])       // all runs across workflows, used by the list overview
const batchActiveTab = ref('overview')
const selectedHistoryTaskId = ref('')
const historyActiveTab = ref('compare')
const actionStatus = reactive({
  type: 'idle',
  title: '等待操作',
  message: '保存任务后可执行对比、预览、后台执行或复制任务。',
})

const state = reactive({
  datasources: [],
  tasks: [],
  workflows: [],
  drivers: {},
  dbTypes: [],
  history: [],
  historySheets: [],
})

const taskDraft = reactive({
  name: '',
  source_kind: 'sql',
  target_kind: 'sql',
  source_id: '',
  target_id: '',
  sql_mode: 'single',
  source_sql: '',
  target_sql: '',
  // Excel-side fields. excel_filename and excel_sheets are UI-only — the
  // backend persists path/sheet/header_row, the rest is for showing
  // upload state and the sheet dropdown.
  source_excel_path: '',
  source_excel_filename: '',
  source_excel_sheets: [],
  source_sheet: '',
  source_header_row: 1,
  target_excel_path: '',
  target_excel_filename: '',
  target_excel_sheets: [],
  target_sheet: '',
  target_header_row: 1,
  key_columns: '',
  ignore_columns: '',
  column_mappings: '',
  numeric_tolerance: '',
  trim_strings: false,
  case_insensitive: false,
  empty_as_null: false,
  max_rows: 100000,
  export_max_rows: 50000,
  fetch_chunk_size: 5000,
  stream_compare: false,
})

const datasourceDraft = reactive({
  name: '',
  db_type: 'mysql',
  host: '',
  port: 3306,
  database: '',
  username: '',
  password: '',
})

const workflowDraft = reactive({
  name: '',
  default_variables: '',
  runtime_variables: '',
  nodes: [],
})

const editingDatasourceId = ref('')
const editDraft = reactive({ name: '', db_type: '', host: '', port: 3306, database: '', username: '', password: '' })

const lineage = reactive({
  sql: '',
  dialect: '',
  schemaDatasourceId: '',
  schemaName: '',
  schemaTableFilter: '',
  schemaOnlySqlTables: true,
  schemaDialect: '',
  schemaFiles: [],
  sqlFile: null,
  result: null,
  error: '',
})

const batch = reactive({
  dialect: '',
  schemaDatasourceId: '',
  schemaName: '',
  schemaTableFilter: '',
  schemaOnlySqlTables: true,
  schemaDialect: '',
  schemaFiles: [],
  files: [],
  result: null,
  exports: null,
  error: '',
})

const currentTask = computed(() => state.tasks.find((task) => task.id === selectedTaskId.value))
const isSavedTask = computed(() => selectedTaskId.value !== 'new')
const currentWorkflow = computed(() => state.workflows.find((wf) => wf.id === selectedWorkflowId.value))
const isSavedWorkflow = computed(() => selectedWorkflowId.value !== 'new')
const selectedHistory = ref(new Set())
const selectedSheets = ref(new Set(['汇总对照']))
const historyTaskOptions = computed(() => {
  const options = state.tasks.map((task) => ({ id: task.id, name: task.name }))
  const seen = new Set(options.map((item) => item.id))
  state.history.forEach((item) => {
    if (item.task_id && !seen.has(item.task_id)) {
      seen.add(item.task_id)
      options.push({ id: item.task_id, name: item.task_name || `已删除任务 ${item.task_id.slice(0, 8)}` })
    }
  })
  return options
})
const filteredHistory = computed(() => {
  let items = state.history
  if (historyActiveTab.value === 'compare') {
    items = items.filter((item) => item.type !== 'lineage')
  } else if (historyActiveTab.value === 'lineage') {
    items = items.filter((item) => item.type === 'lineage')
  }
  if (!selectedHistoryTaskId.value) return items
  return items.filter((item) => item.task_id === selectedHistoryTaskId.value)
})
const compareHistoryCount = computed(() => state.history.filter((item) => item.type !== 'lineage').length)
const lineageHistoryCount = computed(() => state.history.filter((item) => item.type === 'lineage').length)

const driverItems = computed(() => Object.entries(state.drivers || {}))
const activeViewLabel = computed(() => views.find((view) => view.id === activeView.value)?.label || '数据源管理')
const batchSelectedFileNames = computed(() => batch.files.map((file) => file.name))
const batchTabs = [
  { id: 'overview', label: '流程总览' },
  { id: 'graph', label: '数据流图' },
  { id: 'files', label: '脚本清单' },
  { id: 'edges', label: '表级流转' },
  { id: 'deps', label: '跨脚本依赖' },
  { id: 'dag', label: 'DAG 分析' },
  { id: 'warnings', label: '风险提示' },
]
const compareBuckets = [
  { id: 'only_source', label: '只在源端' },
  { id: 'only_target', label: '只在目标端' },
  { id: 'diff', label: '差异' },
  { id: 'same', label: '一致' },
]

// Field picker — union of source/target fields with which-side badges, mapped
// to taskDraft.ignore_columns. Matching is case-insensitive + trim, mirroring
// _normalize_column_name() in app/compare/engine.py.
const normalizeColumn = (col) => String(col || '').trim().toLowerCase()
const ignoredColumnSet = computed(() =>
  new Set(parseCsv(taskDraft.ignore_columns).map(normalizeColumn))
)
const fieldPickerRows = computed(() => {
  const sourceSet = new Set(sourceFields.value.map(normalizeColumn))
  const targetSet = new Set(targetFields.value.map(normalizeColumn))
  const order = []
  const seen = new Set()
  const push = (name) => {
    const key = normalizeColumn(name)
    if (!key || seen.has(key)) return
    seen.add(key)
    order.push({
      name,
      key,
      onSource: sourceSet.has(key),
      onTarget: targetSet.has(key),
    })
  }
  sourceFields.value.forEach(push)
  targetFields.value.forEach(push)
  return order.map((row) => ({ ...row, included: !ignoredColumnSet.value.has(row.key) }))
})
const fieldPickerHasFields = computed(() => fieldPickerRows.value.length > 0)
const toggleFieldIncluded = (name) => {
  const key = normalizeColumn(name)
  const currentIgnore = parseCsv(taskDraft.ignore_columns)
  const without = currentIgnore.filter((col) => normalizeColumn(col) !== key)
  if (without.length < currentIgnore.length) {
    taskDraft.ignore_columns = without.join(', ')
  } else {
    taskDraft.ignore_columns = [...currentIgnore, name].join(', ')
  }
}
const fieldPickerSelectAll = () => { taskDraft.ignore_columns = '' }
const fieldPickerExcludeOneSided = () => {
  const oneSided = fieldPickerRows.value
    .filter((row) => !(row.onSource && row.onTarget))
    .map((row) => row.name)
  taskDraft.ignore_columns = oneSided.join(', ')
}

const loadBootstrap = async ({ keepTaskSelection = false } = {}) => {
  const previousTaskId = selectedTaskId.value
  loading.value = true
  try {
    const data = await apiGet('/api/bootstrap')
    state.datasources = data.datasources || []
    state.tasks = data.tasks || []
    state.workflows = data.workflows || []
    state.drivers = data.drivers || {}
    state.dbTypes = data.db_types || []
    state.history = data.history || []
    state.historySheets = data.history_sheets || []
    if (state.dbTypes.length) datasourceDraft.db_type = state.dbTypes[0]
    if (state.datasources.length) {
      taskDraft.source_id = state.datasources[0].id
      taskDraft.target_id = state.datasources[0].id
    }
    if (state.tasks.length && !keepTaskSelection) {
      selectTask(state.tasks[0].id)
    } else if (!state.tasks.length && !keepTaskSelection) {
      selectTask('new')
    } else if (keepTaskSelection && previousTaskId !== 'new') {
      const refreshedTask = state.tasks.find((task) => task.id === previousTaskId)
      if (refreshedTask) {
        selectedTaskId.value = previousTaskId
        fillDraft(refreshedTask)
      }
    }
  } finally {
    loading.value = false
  }
}

const parseCsv = (value) => String(value || '').split(',').map((item) => item.trim()).filter(Boolean)
const parseMappings = (value) => {
  const result = {}
  String(value || '').split('\n').forEach((line) => {
    const [source, target] = line.split('->').map((item) => item?.trim())
    if (source && target) result[source] = target
  })
  return result
}

const setActionStatus = (type, title, message = '') => {
  actionStatus.type = type
  actionStatus.title = title
  actionStatus.message = message
}

const setNotice = (msg) => {
  notice.value = msg
  if (noticeTimer.value) clearTimeout(noticeTimer.value)
  if (msg) noticeTimer.value = setTimeout(() => { notice.value = '' }, 4000)
}

const stopAsyncPoll = () => {
  if (asyncPollTimer.value) {
    clearInterval(asyncPollTimer.value)
    asyncPollTimer.value = null
  }
}

const toErrorMessage = (error) => error?.message || String(error || '未知错误')
const historyItemTaskLabel = (item) => item.task_name || (item.task_id ? `任务 ${item.task_id.slice(0, 8)}` : '非对比任务')
const summaryValue = (item, key) => item.summary?.[key] ?? '-'

const { copy: _clipboardCopy } = useClipboard({ legacy: true })
const copyField = async (text) => {
  if (!text) return
  try {
    await _clipboardCopy(text)
    setNotice(`已复制：${text}`)
  } catch {
    setNotice('复制失败，请手动选中复制')
  }
}

const loadHistory = async () => {
  state.history = await apiGet('/api/history')
  selectedHistory.value = new Set()
}

const deleteHistory = async (runId) => {
  try {
    await apiJson(`/api/history/${runId}`, 'DELETE')
    state.history = state.history.filter(h => h.run_id !== runId)
    selectedHistory.value.delete(runId)
  } catch (error) {
    setNotice(`删除失败：${toErrorMessage(error)}`)
  }
}

const taskPayload = () => ({
  name: taskDraft.name,
  source_kind: taskDraft.source_kind,
  target_kind: taskDraft.target_kind,
  source_id: taskDraft.source_id,
  target_id: taskDraft.target_id,
  sql_mode: taskDraft.sql_mode,
  source_sql: taskDraft.source_sql,
  target_sql: taskDraft.target_sql,
  source_excel_path: taskDraft.source_excel_path,
  source_sheet: taskDraft.source_sheet,
  source_header_row: Number(taskDraft.source_header_row) || 1,
  target_excel_path: taskDraft.target_excel_path,
  target_sheet: taskDraft.target_sheet,
  target_header_row: Number(taskDraft.target_header_row) || 1,
  key_columns: parseCsv(taskDraft.key_columns),
  rules: {
    ignore_columns: parseCsv(taskDraft.ignore_columns),
    column_mappings: parseMappings(taskDraft.column_mappings),
    numeric_tolerance: taskDraft.numeric_tolerance === '' ? null : Number(taskDraft.numeric_tolerance),
    trim_strings: taskDraft.trim_strings,
    case_insensitive: taskDraft.case_insensitive,
    empty_as_null: taskDraft.empty_as_null,
  },
  limits: {
    max_rows: Number(taskDraft.max_rows),
    export_max_rows: Number(taskDraft.export_max_rows),
    fetch_chunk_size: Number(taskDraft.fetch_chunk_size),
    stream_compare: taskDraft.stream_compare,
  },
})

const fillDraft = (task) => {
  Object.assign(taskDraft, {
    name: task?.name || '',
    source_kind: task?.source_kind || 'sql',
    target_kind: task?.target_kind || 'sql',
    source_id: task?.source_id || state.datasources[0]?.id || '',
    target_id: task?.target_id || state.datasources[0]?.id || '',
    sql_mode: task?.sql_mode || 'single',
    source_sql: task?.source_sql || '',
    target_sql: task?.target_sql || '',
    source_excel_path: task?.source_excel_path || '',
    source_excel_filename: task?.source_excel_path ? task.source_excel_path.split('/').pop() : '',
    source_excel_sheets: [],
    source_sheet: task?.source_sheet || '',
    source_header_row: task?.source_header_row || 1,
    target_excel_path: task?.target_excel_path || '',
    target_excel_filename: task?.target_excel_path ? task.target_excel_path.split('/').pop() : '',
    target_excel_sheets: [],
    target_sheet: task?.target_sheet || '',
    target_header_row: task?.target_header_row || 1,
    key_columns: (task?.key_columns || []).join(', '),
    ignore_columns: (task?.rules?.ignore_columns || []).join(', '),
    column_mappings: Object.entries(task?.rules?.column_mappings || {}).map(([s, t]) => `${s} -> ${t}`).join('\n'),
    numeric_tolerance: task?.rules?.numeric_tolerance ?? '',
    trim_strings: Boolean(task?.rules?.trim_strings),
    case_insensitive: Boolean(task?.rules?.case_insensitive),
    empty_as_null: Boolean(task?.rules?.empty_as_null),
    max_rows: task?.limits?.max_rows || 100000,
    export_max_rows: task?.limits?.export_max_rows || 50000,
    fetch_chunk_size: task?.limits?.fetch_chunk_size || 5000,
    stream_compare: Boolean(task?.limits?.stream_compare),
  })
}

const selectTask = (id) => {
  stopAsyncPoll()
  selectedTaskId.value = id
  previewOutput.value = ''
  sourcePreviewData.value = null
  targetPreviewData.value = null
  sourceFields.value = []
  targetFields.value = []
  compareResult.value = null
  asyncStatus.value = null
  setActionStatus(
    id === 'new' ? 'idle' : 'ready',
    id === 'new' ? '新建任务' : '任务已载入',
    id === 'new' ? '填写任务信息后点击保存任务。' : '可以执行、预览、后台执行或复制当前任务。',
  )
  fillDraft(id === 'new' ? null : state.tasks.find((task) => task.id === id))
}

const saveTask = async () => {
  setNotice('保存中...')
  setActionStatus('running', '正在保存任务', '正在校验数据源、SQL 和主键配置。')
  try {
    if (selectedTaskId.value === 'new') {
      const created = await apiJson('/api/tasks', 'POST', taskPayload())
      state.tasks.unshift(created)
      selectTask(created.id)
    } else {
      const updated = await apiJson(`/api/tasks/${selectedTaskId.value}`, 'PUT', taskPayload())
      const index = state.tasks.findIndex((task) => task.id === selectedTaskId.value)
      state.tasks[index] = updated
    }
    setNotice('任务已保存')
    setActionStatus('success', '任务已保存', '现在可以执行对比、预览或后台执行。')
  } catch (error) {
    setNotice('保存失败')
    previewOutput.value = toErrorMessage(error)
    setActionStatus('error', '保存失败', toErrorMessage(error))
  }
}

const deleteTask = async () => {
  if (selectedTaskId.value === 'new') return
  setActionStatus('running', '正在删除任务')
  try {
    await apiJson(`/api/tasks/${selectedTaskId.value}`, 'DELETE')
    state.tasks = state.tasks.filter((task) => task.id !== selectedTaskId.value)
    selectTask(state.tasks[0]?.id || 'new')
    setActionStatus('success', '任务已删除')
  } catch (error) {
    previewOutput.value = toErrorMessage(error)
    setActionStatus('error', '删除失败', toErrorMessage(error))
  }
}

const copyTask = async () => {
  if (selectedTaskId.value === 'new') return
  setActionStatus('running', '正在复制任务', currentTask.value?.name || '')
  try {
    const copied = await apiJson(`/api/tasks/${selectedTaskId.value}/copy`, 'POST')
    state.tasks.unshift(copied)
    selectTask(copied.id)
    setActionStatus('success', '任务已复制', `已创建：${copied.name}`)
  } catch (error) {
    previewOutput.value = toErrorMessage(error)
    setActionStatus('error', '复制失败', toErrorMessage(error))
  }
}

const uploadExcel = async (side, file) => {
  if (!file) return
  setActionStatus('running', `上传 ${side === 'source' ? '源' : '目标'} Excel`, file.name)
  try {
    const form = new FormData()
    form.append('file', file)
    const response = await apiForm('/api/uploads/excel', form)
    const prefix = side === 'source' ? 'source' : 'target'
    taskDraft[`${prefix}_excel_path`] = response.path
    taskDraft[`${prefix}_excel_filename`] = response.filename
    taskDraft[`${prefix}_excel_sheets`] = response.sheets
    if (!taskDraft[`${prefix}_sheet`] && response.sheets.length) {
      taskDraft[`${prefix}_sheet`] = response.sheets[0]
    }
    setActionStatus('success', 'Excel 已上传', `${response.filename} · ${response.sheets.length} 个 sheet`)
  } catch (error) {
    setActionStatus('error', 'Excel 上传失败', toErrorMessage(error))
  }
}

const runTask = async () => {
  if (selectedTaskId.value === 'new') return
  asyncStatus.value = null
  previewOutput.value = '执行中...'
  setActionStatus('running', '正在执行对比', '查询源/目标数据并生成 JSON、Excel 结果。')
  try {
    const result = await apiJson(`/api/tasks/${selectedTaskId.value}/run`, 'POST')
    compareResult.value = result
    previewOutput.value = JSON.stringify(result, null, 2)
    setActionStatus('success', '对比完成', `only_source=${result.summary?.only_source ?? 0}，only_target=${result.summary?.only_target ?? 0}，diff=${result.summary?.diff ?? 0}，same=${result.summary?.same ?? 0}`)
    await loadBootstrap({ keepTaskSelection: true })
  } catch (error) {
    previewOutput.value = toErrorMessage(error)
    setActionStatus('error', '执行失败', toErrorMessage(error))
  }
}

const runAsync = async () => {
  if (selectedTaskId.value === 'new') return
  previewOutput.value = ''
  setActionStatus('running', '正在提交后台任务')
  try {
    asyncJob.value = await apiJson(`/api/tasks/${selectedTaskId.value}/run-async`, 'POST')
    asyncStatus.value = asyncJob.value
    setActionStatus('running', '后台任务已提交', `任务号：${asyncJob.value.job_id}`)
    asyncPollTimer.value = setInterval(async () => {
      try {
        asyncStatus.value = await apiGet(`/api/runs/${asyncJob.value.job_id}`)
        if (asyncStatus.value.status === 'success' && asyncStatus.value.result) {
          compareResult.value = asyncStatus.value.result
        }
        setActionStatus(
          ['success', 'failed', 'cancelled'].includes(asyncStatus.value.status) ? (asyncStatus.value.status === 'success' ? 'success' : 'error') : 'running',
          `后台任务状态：${asyncStatus.value.status}`,
          asyncStatus.value.error || `阶段：${asyncStatus.value.stage || '运行中'}`,
        )
        if (['success', 'failed', 'cancelled'].includes(asyncStatus.value.status)) {
          stopAsyncPoll()
          await loadBootstrap({ keepTaskSelection: true })
        }
      } catch (error) {
        stopAsyncPoll()
        setActionStatus('error', '后台状态查询失败', toErrorMessage(error))
      }
    }, 1200)
  } catch (error) {
    asyncStatus.value = null
    setActionStatus('error', '后台任务提交失败', toErrorMessage(error))
  }
}

const cancelAsync = async () => {
  if (!asyncJob.value) return
  setActionStatus('running', '正在取消后台任务')
  try {
    asyncStatus.value = await apiJson(`/api/runs/${asyncJob.value.job_id}/cancel`, 'POST')
    setActionStatus('success', '已发送取消请求', '后台任务会在当前安全阶段结束后停止。')
  } catch (error) {
    setActionStatus('error', '取消失败', toErrorMessage(error))
  }
}

const previewTask = async (side) => {
  if (selectedTaskId.value === 'new') return
  const previewRef = side === 'source' ? sourcePreviewData : targetPreviewData
  previewRef.value = { loading: true }
  const sql = side === 'target' && taskDraft.sql_mode === 'double' ? taskDraft.target_sql : taskDraft.source_sql
  const datasourceId = side === 'target' ? taskDraft.target_id : taskDraft.source_id
  setActionStatus('running', side === 'target' ? '正在预览目标数据' : '正在预览源数据')
  try {
    const result = await apiJson(`/api/tasks/${selectedTaskId.value}/preview`, 'POST', { side, sql, datasource_id: datasourceId, limit: 3 })
    previewRef.value = result
    setActionStatus('success', '预览完成', `返回 ${result.rows?.length ?? 0} 行`)
  } catch (error) {
    previewRef.value = { error: toErrorMessage(error) }
    setActionStatus('error', '预览失败', toErrorMessage(error))
  }
}

const extractFields = async (side) => {
  const kind = taskDraft[`${side}_kind`] || 'sql'
  setActionStatus('running', '正在提取字段')
  try {
    let columns = []
    if (kind === 'excel') {
      const result = await apiJson('/api/preview/columns', 'POST', {
        kind: 'excel',
        excel_path: taskDraft[`${side}_excel_path`],
        sheet: taskDraft[`${side}_sheet`],
        header_row: Number(taskDraft[`${side}_header_row`]) || 1,
      })
      columns = result.columns || []
    } else {
      const sql = side === 'source' ? taskDraft.source_sql : taskDraft.target_sql
      const data = await apiJson('/api/sql/assist', 'POST', { sql, dialect: '' })
      columns = (data.output_columns || []).filter(c => !c.includes('*'))
      if (columns.length === 0) {
        // SELECT * — sqlglot can't resolve, hit the DB via /api/preview/columns.
        // Works whether or not the task is saved (no task_id needed).
        setActionStatus('running', 'SELECT * 检测到，正在查询数据库获取字段...')
        const result = await apiJson('/api/preview/columns', 'POST', {
          kind: 'sql',
          datasource_id: side === 'source' ? taskDraft.source_id : taskDraft.target_id,
          sql,
        })
        columns = result.columns || []
      }
    }
    if (side === 'source') sourceFields.value = columns
    else targetFields.value = columns
    setActionStatus('success', '字段提取完成', `识别字段 ${columns.length} 个`)
  } catch (error) {
    setActionStatus('error', '字段提取失败', toErrorMessage(error))
  }
}

const recommendKey = async () => {
  setActionStatus('running', '正在推荐主键')
  try {
    const data = await apiJson('/api/sql/assist', 'POST', { sql: taskDraft.source_sql, dialect: '' })
    if (data.key_candidates?.length) taskDraft.key_columns = data.key_candidates.join(', ')
    setActionStatus('success', '主键推荐完成', `推荐：${data.key_candidates?.join(', ') || '无候选'}`)
  } catch (error) {
    setActionStatus('error', '主键推荐失败', toErrorMessage(error))
  }
}

const formatSql = async (side) => {
  const sql = side === 'source' ? taskDraft.source_sql : taskDraft.target_sql
  setActionStatus('running', '正在格式化 SQL')
  try {
    const data = await apiJson('/api/sql/assist', 'POST', { sql, dialect: '' })
    if (side === 'source') {
      taskDraft.source_sql = data.formatted_sql || taskDraft.source_sql
    } else {
      taskDraft.target_sql = data.formatted_sql || taskDraft.target_sql
    }
    previewOutput.value = JSON.stringify(data, null, 2)
    setActionStatus('success', 'SQL 已格式化')
  } catch (error) {
    previewOutput.value = toErrorMessage(error)
    setActionStatus('error', 'SQL 格式化失败', toErrorMessage(error))
  }
}

// --- Workflow handlers ---
const parseVariables = (text) => {
  const out = {}
  String(text || '').split('\n').forEach((line) => {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) return
    const eq = trimmed.indexOf('=')
    if (eq < 0) return
    const key = trimmed.slice(0, eq).trim()
    if (key) out[key] = trimmed.slice(eq + 1).trim()
  })
  return out
}
const stringifyVariables = (obj) =>
  Object.entries(obj || {}).map(([k, v]) => `${k}=${v}`).join('\n')

const fillWorkflowDraft = (wf) => {
  workflowDraft.name = wf?.name || ''
  workflowDraft.default_variables = stringifyVariables(wf?.default_variables)
  workflowDraft.runtime_variables = ''
  workflowDraft.nodes = (wf?.nodes || []).map((node) => ({
    id: node.id || '',
    type: node.type || 'compare',
    name: node.name || '',
    // type-specific draft fields, kept flat so the form binds cleanly.
    task_id: node.config?.task_id || '',
    source_sql_override: node.config?.source_sql_override || '',
    target_sql_override: node.config?.target_sql_override || '',
    sql: node.config?.sql || '',
    dialect: node.config?.dialect || '',
    method: node.config?.method || 'GET',
    url: node.config?.url || '',
    body: node.config?.body || '',
    expect_status: node.config?.expect_status ?? '',
    // excel_export: sheets list. Filename 由后端写盘时按 run_id 自动命名。
    sheets: Array.isArray(node.config?.sheets)
      ? node.config.sheets.map((s) => ({ ...s }))
      : [],
    // params node: typed parameter list
    parameters: Array.isArray(node.config?.parameters)
      ? node.config.parameters.map((p) => ({ ...p }))
      : [],
    depends_on: Array.isArray(node.depends_on) ? [...node.depends_on] : [],
    when: node.when || '',
  }))
}

const buildNodeConfig = (node) => {
  if (node.type === 'params') return {
    parameters: (node.parameters || []).map((p) => ({
      name: p.name,
      type: p.type || 'fixed',
      ...(p.default !== undefined && p.default !== '' ? { default: p.default } : {}),
      ...(p.source ? { source: p.source } : {}),
      ...(p.required ? { required: true } : {}),
      ...(p.description ? { description: p.description } : {}),
      ...(p.sql ? { sql: p.sql } : {}),
      ...(p.datasource ? { datasource: p.datasource } : {}),
    })),
  }
  if (node.type === 'compare') return {
    task_id: node.task_id,
    ...(node.source_sql_override ? { source_sql_override: node.source_sql_override } : {}),
    ...(node.target_sql_override ? { target_sql_override: node.target_sql_override } : {}),
  }
  if (node.type === 'lineage') return {
    sql: node.sql,
    ...(node.dialect ? { dialect: node.dialect } : {}),
  }
  if (node.type === 'http') return {
    url: node.url,
    method: node.method || 'GET',
    ...(node.body ? { body: node.body } : {}),
    ...(node.expect_status !== '' && node.expect_status !== null && node.expect_status !== undefined ? { expect_status: Number(node.expect_status) } : {}),
  }
  if (node.type === 'excel_export') return {
    sheets: (node.sheets || []).map((s) => ({
      id: s.id,
      enabled: s.enabled !== false,
      sheet_name: s.sheet_name || s.id,
      source: s.source || 'summary',
      custom_sql: s.custom_sql || '',
      max_rows: Number(s.max_rows) || 100000,
      freeze_header: s.freeze_header !== false,
      auto_width: s.auto_width !== false,
      highlight_diff: s.highlight_diff !== false,
    })),
  }
  return {}
}

const workflowPayload = () => ({
  name: workflowDraft.name,
  default_variables: parseVariables(workflowDraft.default_variables),
  nodes: workflowDraft.nodes.map((node) => ({
    id: node.id,
    type: node.type,
    name: node.name,
    config: buildNodeConfig(node),
    depends_on: Array.isArray(node.depends_on) ? node.depends_on : [],
    when: node.when || '',
  })),
})

const stopWorkflowAsyncPoll = () => {
  if (workflowAsyncPollTimer.value) {
    clearInterval(workflowAsyncPollTimer.value)
    workflowAsyncPollTimer.value = null
  }
}

const selectWorkflow = (id) => {
  stopWorkflowAsyncPoll()
  selectedWorkflowId.value = id
  workflowResult.value = null
  workflowAsyncJob.value = null
  workflowAsyncStatus.value = null
  workflowRunHistory.value = []
  fillWorkflowDraft(id === 'new' ? null : state.workflows.find((wf) => wf.id === id))
  if (id !== 'new') loadWorkflowRunHistory(id)
}

const loadWorkflowRunHistory = async (workflowId) => {
  try {
    workflowRunHistory.value = await apiGet(`/api/workflows/${workflowId}/runs?limit=20`)
  } catch (error) {
    workflowRunHistory.value = []
  }
}

const loadAllWorkflowRuns = async () => {
  try {
    allWorkflowRuns.value = await apiGet('/api/workflow-runs?limit=200')
  } catch (error) {
    allWorkflowRuns.value = []
  }
}

const loadWorkflowRunDetail = async (runId) => {
  try {
    const detail = await apiGet(`/api/workflow-runs/${runId}`)
    workflowResult.value = detail
    workflowAsyncStatus.value = null
    workflowAsyncJob.value = null
    setNotice(`已加载历史运行 ${runId.slice(0, 8)}`)
  } catch (error) {
    setNotice(`加载失败：${toErrorMessage(error)}`)
  }
}

const addWorkflowNode = () => {
  const nextIndex = workflowDraft.nodes.length + 1
  workflowDraft.nodes.push({
    id: `n${nextIndex}`, type: 'compare', name: '', depends_on: [], when: '',
    task_id: '', source_sql_override: '', target_sql_override: '',
    sql: '', dialect: '',
    method: 'GET', url: '', body: '', expect_status: '',
    sheets: [
      { id: 'summary', enabled: true,  sheet_name: '汇总',     source: 'summary',     max_rows: 100000, freeze_header: true, auto_width: true, highlight_diff: true },
      { id: 'diff',    enabled: true,  sheet_name: '差异明细', source: 'diff',        max_rows: 100000, freeze_header: true, auto_width: true, highlight_diff: true },
    ],
    parameters: [],
  })
}

const removeWorkflowNode = (index) => {
  workflowDraft.nodes.splice(index, 1)
}

const moveWorkflowNode = (index, delta) => {
  const target = index + delta
  if (target < 0 || target >= workflowDraft.nodes.length) return
  const tmp = workflowDraft.nodes[index]
  workflowDraft.nodes[index] = workflowDraft.nodes[target]
  workflowDraft.nodes[target] = tmp
}

// Excel-export sheet helpers (only meaningful when node.type === 'excel_export')
const SHEET_TEMPLATES = {
  summary:        { sheet_name: '汇总',         source: 'summary' },
  diff:           { sheet_name: '差异明细',     source: 'diff' },
  only_source:    { sheet_name: '源端缺失',     source: 'only_source' },
  only_target:    { sheet_name: '目标端缺失',   source: 'only_target' },
  new_rows:       { sheet_name: '新增数据',     source: 'new_rows' },
  field_diff:     { sheet_name: '字段级差异',   source: 'field_diff' },
  params:         { sheet_name: '运行参数',     source: 'params' },
  lineage:        { sheet_name: '血缘分析',     source: 'lineage' },
  custom:         { sheet_name: '自定义查询',   source: 'custom' },
}

const addExportSheet = (node, templateId) => {
  const tmpl = SHEET_TEMPLATES[templateId] || SHEET_TEMPLATES.summary
  const id = `${templateId}_${(node.sheets || []).length + 1}`
  ;(node.sheets || (node.sheets = [])).push({
    id,
    enabled: true,
    sheet_name: tmpl.sheet_name,
    source: tmpl.source,
    custom_sql: '',
    max_rows: 100000,
    freeze_header: true,
    auto_width: true,
    highlight_diff: true,
  })
}

const removeExportSheet = (node, sheetIdx) => {
  if (Array.isArray(node.sheets)) node.sheets.splice(sheetIdx, 1)
}

const moveExportSheet = (node, sheetIdx, delta) => {
  const sheets = node.sheets
  if (!Array.isArray(sheets)) return
  const target = sheetIdx + delta
  if (target < 0 || target >= sheets.length) return
  const tmp = sheets[sheetIdx]
  sheets[sheetIdx] = sheets[target]
  sheets[target] = tmp
}

// Params node: parameter list helpers
const addParameter = (node, type = 'fixed') => {
  ;(node.parameters || (node.parameters = [])).push({
    name: '',
    type,
    default: type === 'multi_value' ? [] : '',
    source: type === 'relative_date' ? 'yesterday' : '',
    required: true,
    description: '',
    sql: '',
    datasource: '',
  })
}
const removeParameter = (node, idx) => {
  if (Array.isArray(node.parameters)) node.parameters.splice(idx, 1)
}

const saveWorkflow = async () => {
  setNotice('保存中...')
  try {
    if (selectedWorkflowId.value === 'new') {
      const created = await apiJson('/api/workflows', 'POST', workflowPayload())
      state.workflows.unshift(created)
      selectWorkflow(created.id)
    } else {
      const updated = await apiJson(`/api/workflows/${selectedWorkflowId.value}`, 'PUT', workflowPayload())
      const idx = state.workflows.findIndex((wf) => wf.id === selectedWorkflowId.value)
      if (idx !== -1) state.workflows[idx] = updated
    }
    setNotice('作业流已保存')
  } catch (error) {
    setNotice(`保存失败：${toErrorMessage(error)}`)
  }
}

const deleteWorkflow = async () => {
  if (selectedWorkflowId.value === 'new') return
  try {
    await apiJson(`/api/workflows/${selectedWorkflowId.value}`, 'DELETE')
    state.workflows = state.workflows.filter((wf) => wf.id !== selectedWorkflowId.value)
    selectWorkflow(state.workflows[0]?.id || 'new')
    setNotice('作业流已删除')
  } catch (error) {
    setNotice(`删除失败：${toErrorMessage(error)}`)
  }
}

const runWorkflow = async () => {
  if (selectedWorkflowId.value === 'new') return
  workflowResult.value = null
  workflowAsyncJob.value = null
  workflowAsyncStatus.value = null
  setNotice('执行中...')
  try {
    const variables = parseVariables(workflowDraft.runtime_variables)
    workflowResult.value = await apiJson(`/api/workflows/${selectedWorkflowId.value}/run`, 'POST', { variables })
    setNotice(`执行${workflowResult.value.status === 'success' ? '完成' : '失败'}`)
    loadWorkflowRunHistory(selectedWorkflowId.value)
  } catch (error) {
    setNotice(`执行失败：${toErrorMessage(error)}`)
  }
}

const runWorkflowAsync = async () => {
  if (selectedWorkflowId.value === 'new') return
  workflowResult.value = null
  workflowAsyncStatus.value = null
  try {
    const variables = parseVariables(workflowDraft.runtime_variables)
    workflowAsyncJob.value = await apiJson(`/api/workflows/${selectedWorkflowId.value}/run-async`, 'POST', { variables })
    workflowAsyncStatus.value = workflowAsyncJob.value
    workflowAsyncPollTimer.value = setInterval(async () => {
      try {
        workflowAsyncStatus.value = await apiGet(`/api/runs/${workflowAsyncJob.value.job_id}`)
        if (['success', 'failed', 'cancelled'].includes(workflowAsyncStatus.value.status)) {
          stopWorkflowAsyncPoll()
          loadWorkflowRunHistory(selectedWorkflowId.value)
        }
      } catch (error) {
        stopWorkflowAsyncPoll()
        setNotice(`后台状态查询失败：${toErrorMessage(error)}`)
      }
    }, 1200)
  } catch (error) {
    setNotice(`后台执行提交失败：${toErrorMessage(error)}`)
  }
}

const cancelWorkflowAsync = async () => {
  if (!workflowAsyncJob.value) return
  try {
    workflowAsyncStatus.value = await apiJson(`/api/runs/${workflowAsyncJob.value.job_id}/cancel`, 'POST')
  } catch (error) {
    setNotice(`取消失败：${toErrorMessage(error)}`)
  }
}

const startEditDatasource = (item) => {
  editingDatasourceId.value = item.id
  Object.assign(editDraft, { name: item.name, db_type: item.db_type, host: item.host, port: item.port, database: item.database, username: item.username, password: '' })
}

const cancelEditDatasource = () => { editingDatasourceId.value = '' }

const updateDatasource = async (id) => {
  try {
    const updated = await apiJson(`/api/datasources/${id}`, 'PUT', editDraft)
    const idx = state.datasources.findIndex(d => d.id === id)
    if (idx !== -1) state.datasources[idx] = updated
    editingDatasourceId.value = ''
    setNotice('数据源已更新')
  } catch (error) {
    setNotice(`更新失败：${toErrorMessage(error)}`)
  }
}

const deleteDatasource = async (id) => {
  try {
    await apiJson(`/api/datasources/${id}`, 'DELETE')
    state.datasources = state.datasources.filter(d => d.id !== id)
    setNotice('数据源已删除')
  } catch (error) {
    setNotice(`删除失败：${toErrorMessage(error)}`)
  }
}

const createDatasource = async () => {
  await apiJson('/api/datasources', 'POST', datasourceDraft)
  Object.assign(datasourceDraft, { name: '', host: '', database: '', username: '', password: '' })
  await loadBootstrap()
}

const testDatasource = async (id) => {
  try {
    await apiJson(`/api/datasources/${id}/test`, 'POST')
    setNotice('连接成功')
  } catch (error) {
    setNotice(`连接失败：${toErrorMessage(error)}`)
  }
}

const analyzeLineage = async () => {
  lineage.error = ''
  lineage.result = null
  try {
    if (lineage.sqlFile || lineage.schemaFiles.length) {
      const form = new FormData()
      form.append('sql', lineage.sql)
      form.append('dialect', lineage.dialect)
      form.append('schema_datasource_id', lineage.schemaDatasourceId)
      form.append('schema_name', lineage.schemaName)
      form.append('schema_table_filter', lineage.schemaTableFilter)
      form.append('schema_only_sql_tables', lineage.schemaOnlySqlTables ? 'true' : '')
      form.append('schema_dialect', lineage.schemaDialect)
      if (lineage.sqlFile) form.append('sql_file', lineage.sqlFile)
      lineage.schemaFiles.forEach((file) => form.append('schema_file', file))
      lineage.result = await apiForm('/api/lineage/analyze-form', form)
    } else {
      lineage.result = await apiJson('/api/lineage/analyze', 'POST', {
        sql: lineage.sql,
        dialect: lineage.dialect,
        schema_datasource_id: lineage.schemaDatasourceId,
        schema_name: lineage.schemaName,
        schema_table_filter: lineage.schemaTableFilter,
        schema_only_sql_tables: lineage.schemaOnlySqlTables ? 'true' : '',
        schema_dialect: lineage.schemaDialect,
        schema: '',
      })
    }
  } catch (error) {
    lineage.error = error.message
  }
}

const analyzeBatch = async () => {
  batch.error = ''
  batch.result = null
  const form = new FormData()
  form.append('dialect', batch.dialect)
  form.append('schema_datasource_id', batch.schemaDatasourceId)
  form.append('schema_name', batch.schemaName)
  form.append('schema_table_filter', batch.schemaTableFilter)
  form.append('schema_only_sql_tables', batch.schemaOnlySqlTables ? 'true' : '')
  form.append('schema_dialect', batch.schemaDialect)
  batch.files.forEach((file) => form.append('sql_files', file))
  batch.schemaFiles.forEach((file) => form.append('schema_file', file))
  try {
    const payload = await apiForm('/api/lineage/batch/analyze', form)
    batch.result = payload.result
    batch.exports = payload.exports
  } catch (error) {
    batch.error = error.message
  }
}

const exportHistory = async () => {
  if (!selectedHistory.value.size) {
    setNotice('请先选择要导出的历史记录')
    return
  }
  const form = new FormData()
  Array.from(selectedHistory.value).forEach((id) => form.append('run_ids', id))
  Array.from(selectedSheets.value).forEach((sheet) => form.append('sheet_names', sheet))
  const response = await fetch('/history/export', { method: 'POST', body: form })
  if (!response.ok) throw new Error(await response.text())
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  window.open(url, '_blank')
}

watch(activeView, () => { stopAsyncPoll(); stopWorkflowAsyncPoll() })
onMounted(loadBootstrap)
onUnmounted(() => { stopAsyncPoll(); stopWorkflowAsyncPoll() })

// Shared context for view components. Reactive objects (state, lineage, batch, ...)
// preserve reactivity through inject; refs auto-unwrap in templates.
provide('app', {
  // navigation / shell state
  views, activeView, activeViewLabel, loading, notice,
  // domain state
  state, taskDraft, datasourceDraft, editDraft, editingDatasourceId,
  selectedTaskId, currentTask, isSavedTask,
  sourcePreviewData, targetPreviewData, sourceFields, targetFields,
  compareResult, asyncJob, asyncStatus, previewOutput, actionStatus,
  lineage, batch, batchActiveTab, batchTabs,
  selectedHistory, selectedSheets, selectedHistoryTaskId, historyActiveTab,
  // computed
  driverItems, historyTaskOptions, filteredHistory,
  compareHistoryCount, lineageHistoryCount, compareBuckets,
  batchSelectedFileNames,
  fieldPickerRows, fieldPickerHasFields,
  toggleFieldIncluded, fieldPickerSelectAll, fieldPickerExcludeOneSided,
  // handlers — bootstrap / utils
  loadBootstrap, setNotice, setActionStatus, toErrorMessage,
  historyItemTaskLabel, summaryValue, copyField,
  // handlers — task workbench
  taskPayload, fillDraft, selectTask, saveTask, deleteTask, copyTask,
  runTask, runAsync, cancelAsync, previewTask, extractFields, uploadExcel,
  recommendKey, formatSql,
  // handlers — datasource
  startEditDatasource, cancelEditDatasource, updateDatasource,
  deleteDatasource, createDatasource, testDatasource,
  // handlers — lineage / batch
  analyzeLineage, analyzeBatch,
  // handlers — history
  loadHistory, deleteHistory, exportHistory,
  // workflow state + handlers
  workflowDraft, selectedWorkflowId, currentWorkflow, isSavedWorkflow,
  workflowResult, workflowAsyncJob, workflowAsyncStatus, workflowRunHistory, allWorkflowRuns,
  selectWorkflow, saveWorkflow, deleteWorkflow,
  runWorkflow, runWorkflowAsync, cancelWorkflowAsync,
  addWorkflowNode, removeWorkflowNode, moveWorkflowNode,
  addExportSheet, removeExportSheet, moveExportSheet, SHEET_TEMPLATES,
  addParameter, removeParameter,
  loadWorkflowRunHistory, loadWorkflowRunDetail, loadAllWorkflowRuns,
})
</script>

<template>
  <div class="flex h-screen overflow-hidden bg-slate-50 text-slate-900">
    <aside class="flex w-64 shrink-0 flex-col border-r border-slate-800 bg-slate-900 text-slate-300">
      <div class="flex items-center gap-3 border-b border-slate-800 p-6">
        <div class="grid h-10 w-10 place-items-center rounded-lg bg-blue-600 text-sm font-black text-white">DB</div>
        <div>
          <h1 class="text-xl font-bold tracking-tight text-white">DataOps Studio</h1>
          <p class="text-[11px] text-slate-500">比对 · 血缘 · ETL · 元数据</p>
        </div>
      </div>

      <nav class="flex-1 space-y-1 py-6">
        <button
          v-for="view in views"
          :key="view.id"
          class="group flex w-full items-center px-6 py-3 text-left text-sm font-semibold transition-all hover:bg-slate-800/50 hover:text-white"
          :class="activeView === view.id ? 'border-l-4 border-blue-500 bg-blue-500/10 text-blue-400' : 'border-l-4 border-transparent text-slate-400'"
          @click="activeView = view.id"
        >
          <span class="grid h-7 w-7 place-items-center rounded-lg bg-slate-800 text-[11px] font-black text-slate-300 transition group-hover:bg-slate-700">{{ view.label.slice(0, 1) }}</span>
          <span class="ml-4">{{ view.label }}</span>
        </button>
      </nav>

      <div class="m-4 rounded-xl border border-slate-700/60 bg-slate-800/50 p-4">
        <div class="mb-3 flex items-center justify-between">
          <span class="text-[10px] font-bold uppercase tracking-wider text-slate-500">数据库驱动检测</span>
          <button class="text-[10px] font-bold text-slate-500 transition hover:text-white" @click="loadBootstrap">刷新</button>
        </div>
        <div class="grid grid-cols-2 gap-2 text-[11px]">
          <div v-for="[name, info] in driverItems" :key="name" class="flex items-center truncate">
            <span class="mr-2 h-2 w-2 shrink-0 rounded-full" :class="info.available ? 'bg-green-500' : 'bg-slate-600'"></span>
            <span :class="info.available ? 'text-slate-300' : 'text-slate-500'">{{ name }}</span>
          </div>
        </div>
      </div>
    </aside>

    <main class="flex min-w-0 flex-1 flex-col">
      <header class="z-10 flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-8 shadow-sm">
        <div class="flex items-center gap-2">
          <span class="text-slate-400">/</span>
          <h2 class="text-sm font-bold uppercase tracking-wide text-slate-700">{{ activeViewLabel }}</h2>
          <span v-if="loading" class="ml-3 rounded-full bg-blue-50 px-3 py-1 text-xs font-bold text-blue-600">加载中</span>
        </div>
        <div class="flex items-center gap-3">
          <a class="rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-600" href="/config/export">配置文件导出</a>
        </div>
      </header>

      <div class="min-h-0 flex-1 overflow-y-auto bg-slate-50/70 p-8">
        <div v-if="notice" class="mb-5 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm font-semibold text-blue-700">{{ notice }}</div>

        <DatasourceView v-if="activeView === 'datasource'" />

        <WorkbenchView v-if="activeView === 'workbench'" />

        <WorkflowView v-if="activeView === 'workflow'" />

        <LineageView v-if="activeView === 'lineage'" />

        <BatchView v-if="activeView === 'batch'" />

        <HistoryView v-if="activeView === 'history'" />
      </div>
    </main>

  </div>
</template>
