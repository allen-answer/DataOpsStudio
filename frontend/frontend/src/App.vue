<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import { apiForm, apiGet, apiJson } from './api'
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

        <section v-if="activeView === 'datasource'" class="space-y-6">
          <div class="flex items-end justify-between">
            <div>
              <h2 class="text-2xl font-bold text-slate-800">数据源配置</h2>
              <p class="mt-1 text-sm text-slate-500">管理对比任务所需的数据库连接信息，驱动状态来自后端容器实际检测。</p>
            </div>
            <button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="loadBootstrap">刷新驱动检测</button>
          </div>

          <div class="grid grid-cols-1 gap-4 md:grid-cols-4">
            <div v-for="[name, info] in driverItems" :key="name" class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div class="mb-3 flex items-center justify-between">
                <strong class="text-slate-800">{{ name }}</strong>
                <span class="h-3 w-3 rounded-full" :class="info.available ? 'bg-green-500 shadow-[0_0_0_4px_rgba(34,197,94,.12)]' : 'bg-slate-300'"></span>
              </div>
              <p class="text-xs text-slate-500">{{ info.available ? `已安装：${info.installed_modules.join(', ')}` : `缺失：${info.candidate_modules.join(' / ')}` }}</p>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h3 class="mb-4 font-bold text-slate-800">新增数据源</h3>
            <div class="grid grid-cols-4 gap-3">
              <input v-model="datasourceDraft.name" class="border-none bg-slate-50" placeholder="名称">
              <select v-model="datasourceDraft.db_type" class="border-none bg-slate-50"><option v-for="type in state.dbTypes" :key="type">{{ type }}</option></select>
              <input v-model="datasourceDraft.host" class="border-none bg-slate-50" placeholder="Host">
              <input v-model="datasourceDraft.port" class="border-none bg-slate-50" type="number" placeholder="Port">
              <input v-model="datasourceDraft.database" class="border-none bg-slate-50" placeholder="数据库 / 服务名">
              <input v-model="datasourceDraft.username" class="border-none bg-slate-50" placeholder="用户名">
              <input v-model="datasourceDraft.password" class="border-none bg-slate-50" type="password" placeholder="密码">
              <button class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="createDatasource">添加数据源</button>
            </div>
          </div>

          <div class="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
            <article v-for="item in state.datasources" :key="item.id" class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:shadow-md">
              <template v-if="editingDatasourceId === item.id">
                <div class="mb-4 flex items-center justify-between">
                  <span class="text-sm font-bold text-slate-700">编辑数据源</span>
                  <button class="text-xs text-slate-400 hover:text-slate-700" @click="cancelEditDatasource">取消</button>
                </div>
                <div class="grid grid-cols-2 gap-2 text-sm">
                  <input v-model="editDraft.name" class="col-span-2 border-none bg-slate-50 px-3 py-2" placeholder="名称">
                  <select v-model="editDraft.db_type" class="border-none bg-slate-50 px-3 py-2"><option v-for="type in state.dbTypes" :key="type">{{ type }}</option></select>
                  <input v-model="editDraft.port" class="border-none bg-slate-50 px-3 py-2" type="number" placeholder="Port">
                  <input v-model="editDraft.host" class="col-span-2 border-none bg-slate-50 px-3 py-2" placeholder="Host">
                  <input v-model="editDraft.database" class="col-span-2 border-none bg-slate-50 px-3 py-2" placeholder="数据库 / 服务名">
                  <input v-model="editDraft.username" class="border-none bg-slate-50 px-3 py-2" placeholder="用户名">
                  <input v-model="editDraft.password" class="border-none bg-slate-50 px-3 py-2" type="password" placeholder="密码（留空不修改）">
                </div>
                <div class="mt-4 flex gap-2 border-t border-slate-100 pt-4">
                  <button class="flex-1 rounded-lg bg-blue-600 py-2 text-xs font-bold text-white transition hover:bg-blue-700" @click="updateDatasource(item.id)">保存</button>
                  <button class="flex-1 rounded-lg border border-slate-200 py-2 text-xs font-bold text-slate-600 transition hover:bg-slate-50" @click="cancelEditDatasource">取消</button>
                </div>
              </template>
              <template v-else>
                <div class="mb-4 flex items-start justify-between">
                  <div class="grid h-12 w-12 place-items-center rounded-xl bg-slate-100 text-sm font-black text-slate-600">DS</div>
                  <span class="rounded bg-green-100 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-green-700">已配置</span>
                </div>
                <h3 class="mb-1 font-bold text-slate-800">{{ item.name }}</h3>
                <p class="sql-font mb-4 text-xs text-slate-400">{{ item.db_type }} · {{ item.host }}:{{ item.port }} {{ item.database }}</p>
                <div class="flex gap-2 border-t border-slate-100 pt-4">
                  <button class="flex-1 rounded-lg border border-slate-200 py-2 text-xs font-bold text-slate-600 transition hover:bg-slate-50" @click="testDatasource(item.id)">测试连接</button>
                  <button class="flex-1 rounded-lg border border-slate-200 py-2 text-xs font-bold text-slate-600 transition hover:bg-slate-50" @click="startEditDatasource(item)">编辑</button>
                  <button class="rounded-lg border border-red-100 px-3 py-2 text-xs font-bold text-red-500 transition hover:bg-red-50" @click="deleteDatasource(item.id)">删除</button>
                </div>
              </template>
            </article>
          </div>
        </section>

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

        <section v-if="activeView === 'lineage'" class="space-y-6">
          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div class="mb-6 flex items-end justify-between">
              <div><h2 class="text-2xl font-bold text-slate-800">SQL 血缘分析</h2><p class="mt-1 text-sm text-slate-500">Schema 联动、变量识别、字段级明细和 G6 拓扑图</p></div>
              <button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="analyzeLineage">分析血缘</button>
            </div>
            <SqlEditor v-model="lineage.sql" placeholder="粘贴 SQL，或选择文件" />
            <div class="mt-4 grid grid-cols-4 gap-4">
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">SQL 方言</span><select v-model="lineage.dialect" class="border-none bg-slate-50"><option value="">自动</option><option>mysql</option><option>oracle</option><option>tsql</option><option>postgres</option></select></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">SQL/TXT 文件</span><input type="file" accept=".sql,.txt" class="border-none bg-slate-50" @change="lineage.sqlFile = $event.target.files[0]"></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 数据源</span><select v-model="lineage.schemaDatasourceId" class="border-none bg-slate-50"><option value="">不自动拉取</option><option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option></select><small class="mt-2 block text-xs text-slate-500">失败时会降级为文件元数据。</small></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 元数据</span><input type="file" accept=".json,.sql,.txt,.zip" multiple class="border-none bg-slate-50" @change="lineage.schemaFiles = Array.from($event.target.files)"><small class="mt-2 block text-xs text-slate-500">文件会和数据源字段合并。</small></label>
            </div>
            <div class="mt-4 grid grid-cols-4 gap-4">
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema / Database</span><input v-model="lineage.schemaName" class="border-none bg-slate-50" placeholder="留空使用数据源默认值"></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">表名过滤</span><input v-model="lineage.schemaTableFilter" class="border-none bg-slate-50" placeholder="如 ods_%"></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 方言</span><select v-model="lineage.schemaDialect" class="border-none bg-slate-50"><option value="">跟随数据源</option><option value="mysql">MySQL</option><option value="oracle">Oracle</option><option value="dm">DM</option><option value="ob_mysql">OB MySQL</option><option value="ob_oracle">OB Oracle</option></select></label>
              <label class="flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-600"><input v-model="lineage.schemaOnlySqlTables" type="checkbox" class="h-4 w-4">只拉 SQL 中出现的表</label>
            </div>
            <div v-if="lineageSchemaFileNames.length" class="mt-4 flex flex-wrap gap-2">
              <span v-for="name in lineageSchemaFileNames" :key="name" class="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-600">{{ name }}</span>
            </div>
          </div>
          <div v-if="lineage.error" class="rounded-2xl border border-red-200 bg-red-50 p-5 text-red-700">{{ lineage.error }}</div>
          <div v-if="lineage.result" class="grid gap-6">
            <div
              v-if="lineage.result.warnings?.length || lineage.result.dynamic_sql_segments?.length || lineage.result.parse_errors?.length"
              class="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900 shadow-sm"
            >
              <h2 class="mb-4 text-xl font-bold text-amber-950">解析提示</h2>
              <div class="grid gap-4 xl:grid-cols-3">
                <div v-if="lineage.result.warnings?.length">
                  <h3 class="mb-2 font-bold">风险提示</h3>
                  <ul class="space-y-2">
                    <li v-for="item in lineage.result.warnings" :key="item.type + item.message + item.statement_index" class="rounded-xl bg-white/70 p-3">
                      <strong>{{ item.type }}</strong>
                      <span v-if="item.statement_index" class="ml-2 text-xs text-amber-700">语句 {{ item.statement_index }}</span>
                      <p class="mt-1 text-amber-800">{{ item.message }}</p>
                    </li>
                  </ul>
                </div>
                <div v-if="lineage.result.dynamic_sql_segments?.length">
                  <h3 class="mb-2 font-bold">动态 SQL</h3>
                  <div v-for="item in lineage.result.dynamic_sql_segments" :key="item.sql" class="mb-2 rounded-xl bg-white/70 p-3">
                    <div class="mb-1 text-xs font-bold text-amber-700">{{ item.source }} · {{ item.confidence }}</div>
                    <code class="break-all text-xs">{{ item.sql }}</code>
                  </div>
                </div>
                <div v-if="lineage.result.parse_errors?.length">
                  <h3 class="mb-2 font-bold">解析失败片段</h3>
                  <div v-for="item in lineage.result.parse_errors" :key="item.sql + item.error" class="mb-2 rounded-xl bg-white/70 p-3">
                    <p class="text-amber-800">{{ item.error }}</p>
                    <code class="mt-2 block break-all text-xs">{{ item.sql }}</code>
                  </div>
                </div>
              </div>
            </div>
            <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"><h2 class="mb-4 text-xl font-bold text-slate-800">表级拓扑图</h2><LineageGraph :groups="lineage.result.graph_groups" :edges="lineage.result.graph_edges" /></div>
            <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"><h2 class="mb-4 text-xl font-bold text-slate-800">字段血缘</h2><div class="overflow-auto"><table><thead><tr><th>输出字段</th><th>来源字段</th><th>来源表</th><th>可信度</th><th>变量</th><th>表达式</th></tr></thead><tbody><tr v-for="item in lineage.result.columns" :key="item.output_column + item.expression"><td>{{ item.output_column }}</td><td>{{ item.source_columns.join(', ') }}</td><td>{{ item.source_tables.join(', ') }}</td><td>{{ item.confidence || 'high' }}</td><td>{{ item.variables.join(', ') }}</td><td><code>{{ item.expression }}</code><p v-if="item.warnings?.length" class="mt-1 text-xs text-amber-600">{{ item.warnings.map(w => w.type).join(', ') }}</p></td></tr></tbody></table></div></div>
            <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"><h2 class="mb-4 text-xl font-bold text-slate-800">落表字段映射</h2><div class="overflow-auto"><table><thead><tr><th>目标表</th><th>目标字段</th><th>来源字段</th><th>来源表</th><th>可信度</th><th>处理逻辑</th></tr></thead><tbody><tr v-for="item in lineage.result.insert_mappings" :key="item.target_table + item.target_column + item.position"><td>{{ item.target_table }}</td><td>{{ item.target_column }}</td><td>{{ item.source_columns.join(', ') }}</td><td>{{ item.source_tables.join(', ') }}</td><td>{{ item.confidence || 'high' }}</td><td>{{ item.transform }}<p v-if="item.warnings?.length" class="mt-1 text-xs text-amber-600">{{ item.warnings.map(w => w.type).join(', ') }}</p></td></tr></tbody></table></div></div>
          </div>
        </section>

        <section v-if="activeView === 'batch'" class="space-y-6">
          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div class="mb-6 flex items-end justify-between"><div><h2 class="text-2xl font-bold text-slate-800">多脚本 ETL 流程分析</h2><p class="mt-1 text-sm text-slate-500">支持 .sql/.txt/.zip、Schema 元数据和导出</p></div><button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700" @click="analyzeBatch">分析脚本包</button></div>
            <div class="grid grid-cols-4 gap-4">
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">SQL 方言</span><select v-model="batch.dialect" class="border-none bg-slate-50"><option value="">自动</option><option>mysql</option><option>oracle</option><option>tsql</option><option>postgres</option></select></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">脚本文件</span><input type="file" accept=".sql,.txt,.zip" multiple class="border-none bg-slate-50" @change="batch.files = Array.from($event.target.files)"><small class="mt-2 block text-xs text-slate-500">可一次选择多个 .sql/.txt 文件，或上传一个 .zip 脚本包。</small></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 数据源</span><select v-model="batch.schemaDatasourceId" class="border-none bg-slate-50"><option value="">不自动拉取</option><option v-for="item in state.datasources" :key="item.id" :value="item.id">{{ item.name }}</option></select><small class="mt-2 block text-xs text-slate-500">失败时会降级为文件元数据。</small></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 元数据</span><input type="file" accept=".json,.sql,.txt,.zip" multiple class="border-none bg-slate-50" @change="batch.schemaFiles = Array.from($event.target.files)"><small class="mt-2 block text-xs text-slate-500">文件会和数据源字段合并。</small></label>
            </div>
            <div class="mt-4 grid grid-cols-4 gap-4">
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema / Database</span><input v-model="batch.schemaName" class="border-none bg-slate-50" placeholder="留空使用数据源默认值"></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">表名过滤</span><input v-model="batch.schemaTableFilter" class="border-none bg-slate-50" placeholder="如 dwd_%"></label>
              <label><span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">Schema 方言</span><select v-model="batch.schemaDialect" class="border-none bg-slate-50"><option value="">跟随数据源</option><option value="mysql">MySQL</option><option value="oracle">Oracle</option><option value="dm">DM</option><option value="ob_mysql">OB MySQL</option><option value="ob_oracle">OB Oracle</option></select></label>
              <label class="flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-600"><input v-model="batch.schemaOnlySqlTables" type="checkbox" class="h-4 w-4">只拉脚本中出现的表</label>
            </div>
            <div v-if="batchSelectedFileNames.length" class="mt-4 flex flex-wrap gap-2">
              <span v-for="name in batchSelectedFileNames" :key="name" class="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-600">{{ name }}</span>
            </div>
            <div v-if="batchSchemaFileNames.length" class="mt-3 flex flex-wrap gap-2">
              <span v-for="name in batchSchemaFileNames" :key="name" class="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">Schema: {{ name }}</span>
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
              <div class="overflow-auto"><table><thead><tr><th>文件</th><th>类型</th><th>说明</th></tr></thead><tbody><tr v-for="item in batch.result.warnings" :key="item.file_name + item.message"><td><code>{{ item.file_name }}</code></td><td>{{ item.type }}</td><td>{{ item.message }}</td></tr><tr v-if="!batch.result.warnings.length"><td colspan="3" class="text-slate-400">暂无提示</td></tr></tbody></table></div>
            </div>
          </div>
        </section>

        <section v-if="activeView === 'history'" class="space-y-6">
          <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div class="mb-5 flex flex-wrap items-end justify-between gap-4">
              <div>
                <h2 class="text-2xl font-bold text-slate-800">执行历史</h2>
                <p class="mt-1 text-sm text-slate-500">数据对比与血缘分析结果分开展示，支持多结果合并导出。</p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button class="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 transition hover:bg-slate-50" @click="loadHistory">刷新历史</button>
                <button class="rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="!selectedHistory.size" @click="exportHistory">导出所选历史</button>
              </div>
            </div>
            <div class="mb-5 flex flex-wrap gap-2">
              <button class="rounded-xl px-4 py-2 text-sm font-bold transition" :class="historyActiveTab === 'compare' ? 'bg-blue-600 text-white shadow-lg shadow-blue-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'" @click="historyActiveTab = 'compare'">数据对比 ({{ compareHistoryCount }})</button>
              <button class="rounded-xl px-4 py-2 text-sm font-bold transition" :class="historyActiveTab === 'lineage' ? 'bg-blue-600 text-white shadow-lg shadow-blue-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'" @click="historyActiveTab = 'lineage'">血缘分析 ({{ lineageHistoryCount }})</button>
            </div>
            <div v-if="historyActiveTab === 'compare'" class="mb-5 grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
              <label>
                <span class="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-400">任务筛选</span>
                <select v-model="selectedHistoryTaskId" class="border-none bg-slate-50 px-4 py-3 focus:ring-2 focus:ring-blue-500">
                  <option value="">全部历史</option>
                  <option v-for="task in historyTaskOptions" :key="task.id" :value="task.id">{{ task.name }}</option>
                </select>
              </label>
              <div class="grid grid-cols-3 gap-3">
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ historyTaskOptions.length }}</strong><span class="text-xs font-semibold text-slate-500">关联任务</span></div>
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ filteredHistory.length }}</strong><span class="text-xs font-semibold text-slate-500">当前历史</span></div>
                <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ selectedHistory.size }}</strong><span class="text-xs font-semibold text-slate-500">已选择</span></div>
              </div>
            </div>
            <div v-if="historyActiveTab === 'lineage'" class="mb-5 grid grid-cols-2 gap-3">
              <div class="rounded-2xl bg-slate-50 p-4"><strong class="block text-2xl text-slate-800">{{ lineageHistoryCount }}</strong><span class="text-xs font-semibold text-slate-500">血缘分析历史</span></div>
            </div>
            <div v-if="historyActiveTab === 'compare'" class="mb-4 flex flex-wrap gap-4"><label v-for="sheet in state.historySheets" :key="sheet" class="flex items-center gap-2 rounded-xl bg-slate-50 px-3 py-2 text-sm"><input class="w-auto" type="checkbox" :checked="selectedSheets.has(sheet)" @change="$event.target.checked ? selectedSheets.add(sheet) : selectedSheets.delete(sheet)">{{ sheet }}</label></div>

            <template v-if="historyActiveTab === 'compare'">
              <div class="grid grid-cols-[44px_1.2fr_1.3fr_repeat(4,90px)_160px] gap-2 rounded-t-2xl border border-b-0 border-slate-200 bg-slate-50 px-3 py-3 text-xs font-black uppercase tracking-wider text-slate-400">
                <span></span><span>运行 ID</span><span>任务</span><span>Diff</span><span>Same</span><span>源行数</span><span>目标行数</span><span>下载</span>
              </div>
            </template>
            <template v-if="historyActiveTab === 'lineage'">
              <div class="grid grid-cols-[1.2fr_1.3fr_90px_90px_repeat(4,90px)_160px] gap-2 rounded-t-2xl border border-b-0 border-slate-200 bg-slate-50 px-3 py-3 text-xs font-black uppercase tracking-wider text-slate-400">
                <span>运行 ID</span><span>时间</span><span>文件数</span><span>成功/失败</span><span>读表</span><span>写表</span><span>表边</span><span>脚本边</span><span>警告</span><span>下载</span>
              </div>
            </template>
            <div class="h-[620px] overflow-auto rounded-b-2xl border border-slate-200 bg-white">
              <div v-if="!filteredHistory.length" class="grid h-full place-items-center text-sm text-slate-400">{{ historyActiveTab === 'compare' ? '暂无数据对比历史' : '暂无血缘分析历史' }}</div>
              <template v-if="historyActiveTab === 'compare'">
                <div v-for="(item, idx) in filteredHistory" :key="item.run_id || idx" class="grid w-full grid-cols-[44px_1.2fr_1.3fr_repeat(4,90px)_160px_40px] items-center gap-2 border-b border-slate-100 px-3 py-2 text-sm">
                  <input class="w-auto" type="checkbox" :value="item.run_id" :checked="selectedHistory.has(item.run_id)" @change="$event.target.checked ? selectedHistory.add(item.run_id) : selectedHistory.delete(item.run_id)">
                  <code>{{ item.run_id }}</code>
                  <span>{{ historyItemTaskLabel(item) }}</span>
                  <span>{{ summaryValue(item, 'diff') }}</span>
                  <span>{{ summaryValue(item, 'same') }}</span>
                  <span>{{ item.source_rows }}</span>
                  <span>{{ item.target_rows }}</span>
                  <span class="flex gap-2"><a class="font-semibold text-blue-600" v-if="item.excel_filename" :href="`/results/${item.excel_filename}`">Excel</a><a class="font-semibold text-blue-600" :href="`/results/${item.result_filename}`">JSON</a></span>
                  <button class="text-slate-300 transition hover:text-red-500" title="删除" @click="deleteHistory(item.run_id)">✕</button>
                </div>
              </template>
              <template v-if="historyActiveTab === 'lineage'">
                <div v-for="(item, idx) in filteredHistory" :key="item.run_id || idx" class="grid w-full grid-cols-[1.2fr_1.3fr_90px_90px_repeat(4,90px)_160px_40px] items-center gap-2 border-b border-slate-100 px-3 py-2 text-sm">
                  <code>{{ item.run_id }}</code>
                  <span class="text-xs text-slate-500">{{ item.started_at }}</span>
                  <span>{{ summaryValue(item, 'files') }}</span>
                  <span>{{ summaryValue(item, 'success_files') }} / {{ summaryValue(item, 'failed_files') }}</span>
                  <span>{{ summaryValue(item, 'read_tables') }}</span>
                  <span>{{ summaryValue(item, 'write_tables') }}</span>
                  <span>{{ summaryValue(item, 'table_edges') }}</span>
                  <span>{{ summaryValue(item, 'script_edges') }}</span>
                  <span>{{ summaryValue(item, 'warnings') }}</span>
                  <span class="flex gap-2"><a class="font-semibold text-blue-600" v-if="item.excel_filename" :href="`/results/${item.excel_filename}`">Excel</a><a class="font-semibold text-blue-600" :href="`/results/${item.result_filename}`">JSON</a></span>
                  <button class="text-slate-300 transition hover:text-red-500" title="删除" @click="deleteHistory(item.run_id)">✕</button>
                </div>
              </template>
            </div>
          </div>
        </section>
      </div>
    </main>

  </div>
</template>
