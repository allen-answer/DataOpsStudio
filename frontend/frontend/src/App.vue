<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, provide, reactive, ref, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import { apiForm, apiGet, apiJson } from './api'
import BatchView from './views/BatchView.vue'
import DatasourceView from './views/DatasourceView.vue'
import HistoryView from './views/HistoryView.vue'
import LineageView from './views/LineageView.vue'
const LineageGraph = defineAsyncComponent(() => import('./components/LineageGraph.vue'))
const SqlEditor = defineAsyncComponent(() => import('./components/SqlEditor.vue'))

const views = [
  { id: 'datasource', label: '数据源管理' },
  { id: 'workbench', label: '数据对比任务工作台' },
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
  drivers: {},
  dbTypes: [],
  history: [],
  historySheets: [],
})

const taskDraft = reactive({
  name: '',
  source_id: '',
  target_id: '',
  sql_mode: 'single',
  source_sql: '',
  target_sql: '',
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
const lineageSchemaFileNames = computed(() => lineage.schemaFiles.map((file) => file.name))
const batchSchemaFileNames = computed(() => batch.schemaFiles.map((file) => file.name))
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

const loadBootstrap = async ({ keepTaskSelection = false } = {}) => {
  const previousTaskId = selectedTaskId.value
  loading.value = true
  try {
    const data = await apiGet('/api/bootstrap')
    state.datasources = data.datasources || []
    state.tasks = data.tasks || []
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
  source_id: taskDraft.source_id,
  target_id: taskDraft.target_id,
  sql_mode: taskDraft.sql_mode,
  source_sql: taskDraft.source_sql,
  target_sql: taskDraft.target_sql,
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
    source_id: task?.source_id || state.datasources[0]?.id || '',
    target_id: task?.target_id || state.datasources[0]?.id || '',
    sql_mode: task?.sql_mode || 'single',
    source_sql: task?.source_sql || '',
    target_sql: task?.target_sql || '',
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
  const sql = side === 'source' ? taskDraft.source_sql : taskDraft.target_sql
  setActionStatus('running', '正在提取字段')
  try {
    const data = await apiJson('/api/sql/assist', 'POST', { sql, dialect: '' })
    let columns = (data.output_columns || []).filter(c => !c.includes('*'))

    if (columns.length === 0) {
      if (!isSavedTask.value) {
        setActionStatus('ready', '字段提取', 'SELECT * 需要查询数据库获取字段，请先保存任务')
        return
      }
      setActionStatus('running', 'SELECT * 检测到，正在查询数据库获取字段...')
      const datasourceId = side === 'source' ? taskDraft.source_id : taskDraft.target_id
      const result = await apiJson(`/api/tasks/${selectedTaskId.value}/preview`, 'POST', {
        side, sql, datasource_id: datasourceId, limit: 1,
      })
      if (side === 'source') sourcePreviewData.value = result
      else targetPreviewData.value = result
      columns = Object.keys(result.rows?.[0] ?? {})
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

watch(activeView, stopAsyncPoll)
onMounted(loadBootstrap)
onUnmounted(stopAsyncPoll)

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
  batchSelectedFileNames, lineageSchemaFileNames, batchSchemaFileNames,
  // handlers — bootstrap / utils
  loadBootstrap, setNotice, setActionStatus, toErrorMessage,
  historyItemTaskLabel, summaryValue, copyField,
  // handlers — task workbench
  taskPayload, fillDraft, selectTask, saveTask, deleteTask, copyTask,
  runTask, runAsync, cancelAsync, previewTask, extractFields,
  recommendKey, formatSql,
  // handlers — datasource
  startEditDatasource, cancelEditDatasource, updateDatasource,
  deleteDatasource, createDatasource, testDatasource,
  // handlers — lineage / batch
  analyzeLineage, analyzeBatch,
  // handlers — history
  loadHistory, deleteHistory, exportHistory,
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

        <section v-if="activeView === 'workbench'" class="space-y-6">
          <div class="grid grid-cols-[340px_minmax(0,1fr)] gap-6">
            <aside class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div class="mb-6 flex items-end justify-between">
                <div>
                  <h2 class="text-2xl font-bold text-slate-800">对比任务</h2>
                  <p class="mt-1 text-sm text-slate-500">{{ state.tasks.length }} 个任务 · {{ state.datasources.length }} 个数据源</p>
                </div>
                <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="selectTask('new')">新建</button>
              </div>
              <div class="max-h-[calc(100vh-220px)] space-y-2 overflow-auto pr-1">
                <button
                  v-for="task in state.tasks"
                  :key="task.id"
                  class="w-full rounded-xl border p-4 text-left transition"
                  :class="selectedTaskId === task.id ? 'border-blue-300 bg-blue-50 shadow-sm' : 'border-transparent bg-slate-50 hover:border-slate-200 hover:bg-white'"
                  @click="selectTask(task.id)"
                >
                  <strong class="block text-slate-800">{{ task.name }}</strong>
                  <span class="mt-1 block text-sm text-slate-500">{{ task.sql_mode === 'single' ? '单 SQL' : '双 SQL' }} · keys: {{ task.key_columns.join(', ') }}</span>
                </button>
                <p v-if="!state.tasks.length" class="rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-400">暂无任务，点击新建开始。</p>
              </div>
            </aside>

            <section class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div class="mb-6 flex items-start justify-between gap-4">
                <div>
                  <h2 class="text-2xl font-bold text-slate-800">{{ currentTask?.name || '新增对比任务' }}</h2>
                  <p class="mt-1 text-sm text-slate-500">智能 SQL 工作台 · 预览 / 异步执行 / 字段辅助</p>
                </div>
                <div class="flex flex-wrap justify-end gap-2">
                  <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="saveTask">{{ isSavedTask ? '保存修改' : '保存任务' }}</button>
                  <button class="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedTask" @click="runTask">执行</button>
                  <button class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedTask" @click="runAsync">后台执行</button>
                  <button class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedTask" @click="copyTask">复制</button>
                  <button class="rounded-lg bg-red-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isSavedTask" @click="deleteTask">删除</button>
                </div>
              </div>

              <div class="grid gap-6">
                <div
                  class="rounded-2xl border p-4"
                  :class="{
                    'border-slate-200 bg-slate-50 text-slate-700': actionStatus.type === 'idle' || actionStatus.type === 'ready',
                    'border-blue-200 bg-blue-50 text-blue-800': actionStatus.type === 'running',
                    'border-emerald-200 bg-emerald-50 text-emerald-800': actionStatus.type === 'success',
                    'border-red-200 bg-red-50 text-red-800': actionStatus.type === 'error',
                  }"
                >
                  <div class="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p class="text-sm font-black">{{ actionStatus.title }}</p>
                      <p v-if="actionStatus.message" class="mt-1 text-sm opacity-80">{{ actionStatus.message }}</p>
                    </div>
                    <span v-if="!isSavedTask" class="rounded-full bg-white/70 px-3 py-1 text-xs font-bold">请先保存任务</span>
                  </div>
                </div>

                <div class="grid grid-cols-4 gap-4">
                  <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">任务名称</span><input v-model="taskDraft.name" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500"></label>
                  <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">源数据源</span><select v-model="taskDraft.source_id" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500"><option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
                  <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">目标数据源</span><select v-model="taskDraft.target_id" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500"><option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
                  <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">SQL 模式</span><select v-model="taskDraft.sql_mode" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500"><option value="single">单 SQL</option><option value="double">双 SQL</option></select></label>
                </div>

                <div class="grid grid-cols-1 gap-6 xl:grid-cols-2">
                  <div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div class="mb-4 flex items-center justify-between">
                      <div class="flex items-center gap-2"><span class="h-2 w-2 rounded-full bg-blue-500"></span><span class="text-sm font-bold uppercase tracking-wider text-slate-600">Source SQL</span></div>
                      <div class="flex gap-2"><button class="rounded-lg bg-slate-700/90 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-blue-600" @click="formatSql('source')">格式化</button><button class="rounded-lg bg-slate-700/90 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-blue-600" @click="extractFields('source')">提取字段</button><button class="rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-emerald-700 disabled:opacity-40" :disabled="!isSavedTask" @click="previewTask('source')">预览</button></div>
                    </div>
                    <SqlEditor v-model="taskDraft.source_sql" />
                    <div v-if="sourceFields.length" class="mt-3 flex flex-wrap gap-1.5">
                      <span
                        v-for="col in sourceFields" :key="col"
                        class="cursor-pointer select-all rounded-full bg-slate-700 px-2.5 py-1 text-[11px] font-mono text-slate-200 transition hover:bg-blue-600"
                        :title="'点击复制：' + col"
                        @click="copyField(col)"
                      >{{ col }}</span>
                    </div>
                    <div v-if="sourcePreviewData" class="mt-3">
                      <p v-if="sourcePreviewData.loading" class="text-xs text-slate-400">预览中...</p>
                      <p v-else-if="sourcePreviewData.error" class="rounded-lg bg-red-50 p-2 text-xs text-red-600">{{ sourcePreviewData.error }}</p>
                      <template v-else>
                        <p class="mb-2 text-xs text-slate-400">前 {{ sourcePreviewData.rows?.length ?? 0 }} 行</p>
                        <div class="overflow-x-auto rounded-xl border border-slate-200">
                          <table class="w-full text-xs">
                            <thead><tr class="border-b border-slate-200 bg-slate-50"><th v-for="col in Object.keys(sourcePreviewData.rows?.[0] ?? {})" :key="col" class="px-3 py-2 text-left font-bold text-slate-600">{{ col }}</th></tr></thead>
                            <tbody><tr v-for="(row, i) in sourcePreviewData.rows" :key="i" class="border-b border-slate-100 last:border-0 hover:bg-slate-50"><td v-for="col in Object.keys(sourcePreviewData.rows[0])" :key="col" class="px-3 py-2 text-slate-700">{{ row[col] ?? '' }}</td></tr></tbody>
                          </table>
                        </div>
                      </template>
                    </div>
                  </div>
                  <div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div class="mb-4 flex items-center justify-between">
                      <div class="flex items-center gap-2"><span class="h-2 w-2 rounded-full bg-orange-500"></span><span class="text-sm font-bold uppercase tracking-wider text-slate-600">Target SQL</span></div>
                      <div class="flex gap-2"><button class="rounded-lg bg-slate-700/90 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-blue-600" @click="formatSql('target')">格式化</button><button class="rounded-lg bg-slate-700/90 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-blue-600" @click="extractFields('target')">提取字段</button><button class="rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] font-bold text-white transition hover:bg-emerald-700 disabled:opacity-40" :disabled="!isSavedTask" @click="previewTask('target')">预览</button></div>
                    </div>
                    <SqlEditor v-model="taskDraft.target_sql" placeholder="双 SQL 模式填写" />
                    <div v-if="targetFields.length" class="mt-3 flex flex-wrap gap-1.5">
                      <span
                        v-for="col in targetFields" :key="col"
                        class="cursor-pointer select-all rounded-full bg-slate-700 px-2.5 py-1 text-[11px] font-mono text-slate-200 transition hover:bg-blue-600"
                        :title="'点击复制：' + col"
                        @click="copyField(col)"
                      >{{ col }}</span>
                    </div>
                    <div v-if="targetPreviewData" class="mt-3">
                      <p v-if="targetPreviewData.loading" class="text-xs text-slate-400">预览中...</p>
                      <p v-else-if="targetPreviewData.error" class="rounded-lg bg-red-50 p-2 text-xs text-red-600">{{ targetPreviewData.error }}</p>
                      <template v-else>
                        <p class="mb-2 text-xs text-slate-400">前 {{ targetPreviewData.rows?.length ?? 0 }} 行</p>
                        <div class="overflow-x-auto rounded-xl border border-slate-200">
                          <table class="w-full text-xs">
                            <thead><tr class="border-b border-slate-200 bg-slate-50"><th v-for="col in Object.keys(targetPreviewData.rows?.[0] ?? {})" :key="col" class="px-3 py-2 text-left font-bold text-slate-600">{{ col }}</th></tr></thead>
                            <tbody><tr v-for="(row, i) in targetPreviewData.rows" :key="i" class="border-b border-slate-100 last:border-0 hover:bg-slate-50"><td v-for="col in Object.keys(targetPreviewData.rows[0])" :key="col" class="px-3 py-2 text-slate-700">{{ row[col] ?? '' }}</td></tr></tbody>
                          </table>
                        </div>
                      </template>
                    </div>
                  </div>
                </div>

                <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                  <div class="mb-6 flex items-center gap-3">
                    <span class="grid h-9 w-9 place-items-center rounded-lg bg-slate-100 text-xs font-black text-slate-400">R</span>
                    <h3 class="text-lg font-bold text-slate-700">对比规则设定</h3>
                  </div>
                  <div class="grid grid-cols-1 gap-6 xl:grid-cols-4">
                    <div>
                      <label class="block text-xs font-bold uppercase tracking-wider text-slate-400">主键列</label>
                      <div class="mt-3 flex gap-2">
                        <input v-model="taskDraft.key_columns" class="flex-1 border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500" placeholder="ID, ORDER_NO">
                        <button class="shrink-0 rounded-lg bg-blue-600 px-3 py-2 text-xs font-bold text-white transition hover:bg-blue-700" @click="recommendKey">自动推荐</button>
                      </div>
                      <label class="mt-4 block text-xs font-bold uppercase tracking-wider text-slate-400">忽略字段</label>
                      <input v-model="taskDraft.ignore_columns" class="mt-3 border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500" placeholder="etl_time">
                    </div>
                    <div class="grid grid-cols-2 gap-4 rounded-2xl bg-slate-50 p-4 xl:col-span-2">
                      <label class="flex cursor-pointer items-center rounded-xl border border-transparent bg-white p-3 shadow-sm transition hover:border-blue-200"><input v-model="taskDraft.trim_strings" class="mr-3 h-4 w-4 rounded text-blue-600" type="checkbox"><span class="text-sm font-medium text-slate-600">字符串去空格</span></label>
                      <label class="flex cursor-pointer items-center rounded-xl border border-transparent bg-white p-3 shadow-sm transition hover:border-blue-200"><input v-model="taskDraft.case_insensitive" class="mr-3 h-4 w-4 rounded text-blue-600" type="checkbox"><span class="text-sm font-medium text-slate-600">忽略大小写</span></label>
                      <label class="flex cursor-pointer items-center rounded-xl border border-transparent bg-white p-3 shadow-sm transition hover:border-blue-200"><input v-model="taskDraft.empty_as_null" class="mr-3 h-4 w-4 rounded text-blue-600" type="checkbox"><span class="text-sm font-medium text-slate-600">空字符串视为空值</span></label>
                      <label class="flex cursor-pointer items-center rounded-xl border border-transparent bg-white p-3 shadow-sm transition hover:border-blue-200"><input v-model="taskDraft.stream_compare" class="mr-3 h-4 w-4 rounded text-blue-600" type="checkbox"><span class="text-sm font-medium text-slate-600">流式分块对比</span></label>
                    </div>
                    <div class="grid gap-3">
                      <button class="rounded-2xl bg-blue-600 py-4 font-bold text-white shadow-lg shadow-blue-200 transition hover:-translate-y-0.5 hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" @click="runTask" :disabled="!isSavedTask">开始执行对比</button>
                      <button class="rounded-2xl border border-slate-200 py-3 text-sm font-bold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" @click="copyTask" :disabled="!isSavedTask">复制当前任务</button>
                      <button class="rounded-2xl border border-slate-200 py-3 text-sm font-bold text-slate-700 transition hover:bg-slate-50" @click="saveTask">{{ selectedTaskId === 'new' ? '保存任务' : '保存修改' }}</button>
                    </div>
                  </div>
                  <div class="mt-6 grid grid-cols-4 gap-3">
                    <label><span class="mb-2 block text-xs font-bold text-slate-400">数值容忍</span><input v-model="taskDraft.numeric_tolerance" type="number" step="any" class="border-none bg-slate-50"></label>
                    <label><span class="mb-2 block text-xs font-bold text-slate-400">最大行数</span><input v-model="taskDraft.max_rows" type="number" class="border-none bg-slate-50"></label>
                    <label><span class="mb-2 block text-xs font-bold text-slate-400">导出行数</span><input v-model="taskDraft.export_max_rows" type="number" class="border-none bg-slate-50"></label>
                    <label><span class="mb-2 block text-xs font-bold text-slate-400">分块行数</span><input v-model="taskDraft.fetch_chunk_size" type="number" class="border-none bg-slate-50"></label>
                  </div>
                  <label class="mt-5 block"><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">字段映射</span><textarea v-model="taskDraft.column_mappings" class="min-h-[90px] border-none bg-slate-50 font-mono text-sm" placeholder="source_col -> target_col"></textarea></label>
                </div>

                <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                  <div class="mb-5 flex flex-wrap items-end justify-between gap-3">
                    <div>
                      <h3 class="text-lg font-bold text-slate-800">对比结果预览</h3>
                      <p class="mt-1 text-sm text-slate-500">执行完成后展示汇总、样例明细和下载入口。</p>
                    </div>
                    <div v-if="compareResult" class="flex gap-2">
                      <a class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50" :href="`/results/${compareResult.excel_filename}`">下载 Excel</a>
                      <a class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50" :href="`/results/${compareResult.result_filename}`">下载 JSON</a>
                    </div>
                  </div>
                  <div v-if="compareResult" class="space-y-5">
                    <div class="grid grid-cols-2 gap-3 xl:grid-cols-6">
                      <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ compareResult.summary.only_source }}</strong><span class="text-xs font-semibold text-slate-500">only_source</span></div>
                      <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ compareResult.summary.only_target }}</strong><span class="text-xs font-semibold text-slate-500">only_target</span></div>
                      <div class="rounded-2xl bg-red-50 p-4"><strong class="block text-2xl text-red-700">{{ compareResult.summary.diff }}</strong><span class="text-xs font-semibold text-red-500">diff</span></div>
                      <div class="rounded-2xl bg-emerald-50 p-4"><strong class="block text-2xl text-emerald-700">{{ compareResult.summary.same }}</strong><span class="text-xs font-semibold text-emerald-600">same</span></div>
                      <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ compareResult.source_rows }}</strong><span class="text-xs font-semibold text-slate-500">源行数</span></div>
                      <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ compareResult.target_rows }}</strong><span class="text-xs font-semibold text-slate-500">目标行数</span></div>
                    </div>
                    <div class="grid gap-4 xl:grid-cols-2">
                      <div v-for="bucket in compareBuckets" :key="bucket.id" class="rounded-2xl border border-slate-200 p-4">
                        <div class="mb-3 flex items-center justify-between"><h4 class="font-bold text-slate-700">{{ bucket.label }}</h4><span class="rounded-full bg-slate-100 px-2 py-1 text-xs font-bold text-slate-500">样例 {{ compareResult.samples[bucket.id]?.length || 0 }}</span></div>
                        <pre class="max-h-56 overflow-auto rounded-xl bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(compareResult.samples[bucket.id] || [], null, 2) }}</pre>
                      </div>
                    </div>
                  </div>
                  <pre v-else-if="previewOutput || asyncStatus" class="max-h-[420px] resize-y overflow-auto rounded-2xl bg-slate-950 p-5 text-xs text-slate-100">{{ asyncStatus ? JSON.stringify(asyncStatus, null, 2) : previewOutput }}</pre>
                  <div v-else class=”rounded-2xl border border-dashed border-slate-200 p-8 text-center text-sm text-slate-400”>暂无结果。保存任务后点击”开始执行对比”。</div>
                </div>
                <button v-if="asyncJob" class="w-fit rounded-lg bg-red-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-red-700" @click="cancelAsync">取消后台任务</button>
              </div>
            </section>
          </div>
        </section>

        <LineageView v-if="activeView === 'lineage'" />

        <BatchView v-if="activeView === 'batch'" />

        <HistoryView v-if="activeView === 'history'" />
      </div>
    </main>

  </div>
</template>
