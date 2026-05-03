<script setup>
import { computed, onMounted, onUnmounted, provide, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useClipboard } from '@vueuse/core'
import { storeToRefs } from 'pinia'
import { apiForm, apiGet, apiJson } from './api'
import AppShell from './layouts/AppShell.vue'
// validateTaskDraft 已经迁到 useTaskStore 内部使用
import { useNoticeStore } from './stores/notice'
import { useDatasourceStore } from './stores/datasource'
import { useTaskStore } from './stores/task'

// View 组件不再在这里直接 import；vue-router 接管路由 → 组件渲染（src/router/index.js）。
// AppShell 负责布局壳：sidebar / topbar / 全局 notice / 主内容区（router-view）。

// Pinia stores —— 渐进式拆分，notice / datasource 这两块独立性最强先抽。
// 其它 state（task / workflow / lineage / batch / history）后续轮次再拆。
// inject('app') 仍 backward compat，把 store 暴露的 ref / reactive / 方法平铺过去。
//
// 解构规则：
//   - ref 字段（notice / editingDatasourceId）必须经 storeToRefs 才能保持响应
//   - reactive 字段（actionStatus / datasourceDraft / editDraft）直接解构即可，
//     reactive 对象 destructure 后还是同一个 proxy。混用 storeToRefs 反而会
//     把 reactive 包成 ref，破坏 view 的 `obj.field = x` 直接赋值用法
const noticeStore = useNoticeStore()
const datasourceStore = useDatasourceStore()
const taskStore = useTaskStore()
const { notice } = storeToRefs(noticeStore)
const { actionStatus, setNotice, setActionStatus } = noticeStore
const { editingDatasourceId } = storeToRefs(datasourceStore)
const { datasourceDraft, editDraft } = datasourceStore
// task store 解构：ref 字段经 storeToRefs；reactive (taskDraft) 直接拿
const {
  selectedTaskId, sourceFields, targetFields,
  previewOutput, sourcePreviewData, targetPreviewData,
  compareResult, asyncJob, asyncStatus, asyncPollTimer,
  ignoredColumnSet, fieldPickerRows, fieldPickerHasFields,
  taskValidation, taskValidationIssues, canSaveTask, isSavedTask,
} = storeToRefs(taskStore)
const { taskDraft } = taskStore
const {
  toggleFieldIncluded, fieldPickerSelectAll, fieldPickerExcludeOneSided,
  normalizeColumn, parseCsv, parseMappings,
  fillDraft: storeFillDraft, taskPayload: storeTaskPayload,
  resetSelectionState, stopAsyncPoll: storeStopAsyncPoll,
} = taskStore

const route = useRoute()
const loading = ref(false)
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

const state = reactive({
  datasources: [],
  tasks: [],
  workflows: [],
  drivers: {},
  dbTypes: [],
  history: [],
  historySheets: [],
})

// taskDraft / selectedTaskId / 字段选择 / preview / asyncJob 已迁移到 useTaskStore（顶部解构）

// datasourceDraft / editDraft / editingDatasourceId 已迁移到 useDatasourceStore
// （顶部 storeToRefs 暴露），下面的 startEdit/cancelEdit 也走 store。

const workflowDraft = reactive({
  name: '',
  description: '',
  owner: '',
  tags: [],
  schedule_cron: '',
  project: '',
  status: 'draft',           // draft | active | paused | archived
  input_assets: [],          // [{key, kind, description}]
  output_assets: [],
  default_variables: '',
  runtime_variables: '',
  nodes: [],
})

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

// currentTask 仍依赖 state.tasks（list 还在 App.vue），留在这里 join；
// isSavedTask / taskValidation / taskValidationIssues / canSaveTask 已迁到 useTaskStore。
const currentTask = computed(() => state.tasks.find((task) => task.id === selectedTaskId.value))
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

// Field picker（normalizeColumn / parseCsv / parseMappings / ignoredColumnSet /
// fieldPickerRows / fieldPickerHasFields / toggleFieldIncluded /
// fieldPickerSelectAll / fieldPickerExcludeOneSided）已迁移到 useTaskStore

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

// parseCsv / parseMappings / normalizeColumn 已迁到 useTaskStore（顶部解构）

// setActionStatus / setNotice 迁到 useNoticeStore；stopAsyncPoll 迁到 useTaskStore。
const stopAsyncPoll = storeStopAsyncPoll

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

// taskPayload / fillDraft 已迁到 useTaskStore；fillDraft 接受 fallbackDatasourceId
// 用于"任务里 source_id 为空时退回首个数据源"。
const taskPayload = storeTaskPayload
const fillDraft = (task) => storeFillDraft(task, state.datasources[0]?.id || '')

const selectTask = (id) => {
  stopAsyncPoll()
  selectedTaskId.value = id
  resetSelectionState()
  setActionStatus(
    id === 'new' ? 'idle' : 'ready',
    id === 'new' ? '新建任务' : '任务已载入',
    id === 'new' ? '填写任务信息后点击保存任务。' : '可以执行、预览、后台执行或复制当前任务。',
  )
  fillDraft(id === 'new' ? null : state.tasks.find((task) => task.id === id))
}

const saveTask = async () => {
  // 前端立即校验，省一趟后端往返。后端 _validate_compare_inputs 仍是权威。
  if (!canSaveTask.value) {
    const errs = taskValidationIssues.value.filter(i => i.level === 'error')
    const head = errs[0]?.message || '配置不完整'
    const more = errs.length > 1 ? `（共 ${errs.length} 项问题）` : ''
    setNotice(`保存被拦截：${head}${more}`)
    setActionStatus('error', '保存被拦截', `${head}${more}`)
    return
  }
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

// 主键名称启发式：xxx_id / xxx_no / xxx_code / xxx_key 优先；id / pk / no 也算。
// 跨模式（Excel ↔ SQL）共用此规则——SQL/Excel 字段名通常都遵循类似命名约定。
function pickKeyCandidatesFromFields(fields) {
  const ID_LIKE = /(^|_)(id|pk|key|no|code|sn|uuid|guid)$/i
  return fields.filter(c => ID_LIKE.test(c))
}

const recommendKey = async () => {
  setActionStatus('running', '正在推荐主键')
  try {
    // 当两侧字段都已提取，按"两侧交集 + id-like 命名"推荐，能覆盖 Excel↔SQL 三种交叉模式。
    // 否则回退到 SQL 端的 sqlglot key_candidates（仅 source 是 SQL 时可用）。
    const sourceSet = new Set(sourceFields.value.map(normalizeColumn))
    const targetSet = new Set(targetFields.value.map(normalizeColumn))
    if (sourceSet.size && targetSet.size) {
      const intersect = sourceFields.value.filter(c => targetSet.has(normalizeColumn(c)))
      const candidates = pickKeyCandidatesFromFields(intersect)
      if (candidates.length) {
        taskDraft.key_columns = candidates.join(', ')
        setActionStatus('success', '主键推荐完成', `从两侧交集挑选：${candidates.join(', ')}`)
        return
      }
      // 交集里没有 id-like 列 —— 提示用户手动看，但仍给个降级建议（交集第一列）
      if (intersect.length) {
        taskDraft.key_columns = intersect[0]
        setActionStatus('warning', '主键候选有限',
          `两侧字段交集中未识别到 id 类命名；已临时填入 ${intersect[0]}，请确认后修改`)
        return
      }
      setActionStatus('error', '无法推荐主键',
        '源字段与目标字段没有交集；先在「字段映射」做列对齐，或检查两侧 schema')
      return
    }
    // 退回旧路径：source 是 SQL 时让 sqlglot 抽
    if (taskDraft.source_kind === 'sql' && taskDraft.source_sql) {
      const data = await apiJson('/api/sql/assist', 'POST', { sql: taskDraft.source_sql, dialect: '' })
      if (data.key_candidates?.length) taskDraft.key_columns = data.key_candidates.join(', ')
      setActionStatus('success', '主键推荐完成', `推荐：${data.key_candidates?.join(', ') || '无候选'}`)
      return
    }
    // Excel 端但还没提取字段：先用启发式过 sourceFields（如果已提取）
    if (sourceFields.value.length) {
      const candidates = pickKeyCandidatesFromFields(sourceFields.value)
      if (candidates.length) {
        taskDraft.key_columns = candidates.join(', ')
        setActionStatus('success', '主键推荐完成', `从源字段挑选：${candidates.join(', ')}`)
        return
      }
    }
    setActionStatus('error', '无法推荐主键',
      '请先在「数据来源」点击两侧的「提取字段」加载列名，或手动填主键')
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
  workflowDraft.description = wf?.description || ''
  workflowDraft.owner = wf?.owner || ''
  workflowDraft.tags = Array.isArray(wf?.tags) ? [...wf.tags] : []
  workflowDraft.schedule_cron = wf?.schedule_cron || ''
  workflowDraft.project = wf?.project || ''
  workflowDraft.status = wf?.status || 'draft'
  workflowDraft.input_assets = Array.isArray(wf?.input_assets)
    ? wf.input_assets.map((a) => ({ key: a.key || '', kind: a.kind || 'table', description: a.description || '' }))
    : []
  workflowDraft.output_assets = Array.isArray(wf?.output_assets)
    ? wf.output_assets.map((a) => ({ key: a.key || '', kind: a.kind || 'table', description: a.description || '' }))
    : []
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
    // excel_export sheets. 字段语义：
    //   source_type: 'node_output' | 'history_run'
    //   node_id:     上游节点 id（或历史 run 里的节点 id）
    //   dataset:     compare 简短名 (summary/diff/...) 或顶层字段，runner 自动映射
    //   run_id:      仅 history_run 模式
    // 兼容三个老格式：source_node → node_id, source_field → dataset, source → dataset
    // dataset 老 dot-path 反向归一化：'samples.diff' → 'diff'（让 UI 下拉能选中）
    sheets: Array.isArray(node.config?.sheets)
      ? (() => {
          const deps = Array.isArray(node.depends_on) ? node.depends_on : []
          const singleDep = deps.length === 1 ? deps[0] : ''
          return node.config.sheets.map((s) => {
            let dataset = s.dataset || s.source_field || s.source || ''
            // 把 'samples.<bucket>' 归一化到短名
            const m = /^samples\.(only_source|only_target|diff|same)$/.exec(dataset)
            if (m) dataset = m[1]
            return {
              id: s.id,
              enabled: s.enabled !== false,
              sheet_name: s.sheet_name || s.id,
              source_type: s.source_type || 'node_output',
              node_id: s.node_id || s.source_node || singleDep || '',
              dataset,
              run_id: s.run_id || '',
              max_rows: Number(s.max_rows) || 100000,
            }
          })
        })()
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
      source_type: s.source_type || 'node_output',
      node_id: s.node_id || '',
      dataset: s.dataset || '',
      // run_id 只在 history_run 模式下保存，避免污染 node_output 配置
      ...(s.source_type === 'history_run' && s.run_id ? { run_id: s.run_id } : {}),
      max_rows: Number(s.max_rows) || 100000,
    })),
  }
  return {}
}

const workflowPayload = () => ({
  name: workflowDraft.name,
  description: workflowDraft.description || '',
  owner: workflowDraft.owner || '',
  tags: Array.isArray(workflowDraft.tags) ? workflowDraft.tags.filter(Boolean) : [],
  schedule_cron: workflowDraft.schedule_cron || '',
  project: workflowDraft.project || '',
  status: workflowDraft.status || 'draft',
  // assets：丢掉 key 为空的行，避免后端 min_length 校验失败
  input_assets: (workflowDraft.input_assets || [])
    .filter((a) => a.key && a.key.trim())
    .map((a) => ({ key: a.key.trim(), kind: a.kind || 'table', description: a.description || '' })),
  output_assets: (workflowDraft.output_assets || [])
    .filter((a) => a.key && a.key.trim())
    .map((a) => ({ key: a.key.trim(), kind: a.kind || 'table', description: a.description || '' })),
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
      { id: 'summary', enabled: true, sheet_name: '汇总对照', source_type: 'node_output', node_id: '', dataset: 'summary', run_id: '', max_rows: 100000 },
      { id: 'diff',    enabled: true, sheet_name: '差异明细', source_type: 'node_output', node_id: '', dataset: 'diff',    run_id: '', max_rows: 100000 },
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
// dataset 是面向用户的简洁名（compare 节点的 summary/diff/only_source/only_target/
// same）。后端 runner 自动映射到 samples.* 路径。其他节点类型时 dataset 直接当
// 顶层字段名。
const SHEET_TEMPLATES = {
  summary:        { sheet_name: '汇总对照',     dataset: 'summary' },
  diff:           { sheet_name: '差异明细',     dataset: 'diff' },
  only_source:    { sheet_name: '源端缺失',     dataset: 'only_source' },
  only_target:    { sheet_name: '目标端缺失',   dataset: 'only_target' },
  same:           { sheet_name: '一致行',       dataset: 'same' },
}

const addExportSheet = (node, templateId) => {
  const tmpl = SHEET_TEMPLATES[templateId] || SHEET_TEMPLATES.summary
  const id = `${templateId}_${(node.sheets || []).length + 1}`
  ;(node.sheets || (node.sheets = [])).push({
    id,
    enabled: true,
    sheet_name: tmpl.sheet_name,
    source_type: 'node_output',
    node_id: '',
    dataset: tmpl.dataset,
    run_id: '',
    max_rows: 100000,
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

// 保存前的软校验：找出明显能提前发现的配置问题（不阻断保存，只是
// 提示用户去 UI 修；后端有硬校验做最后一道把关）。
const workflowDraftWarnings = () => {
  const warnings = []
  for (const node of workflowDraft.nodes) {
    if (node.type === 'excel_export') {
      const noDeps = !Array.isArray(node.depends_on) || node.depends_on.length === 0
      for (const s of (node.sheets || [])) {
        if (!s.enabled) continue
        if (s.source_type === 'history_run') {
          if (!s.run_id) warnings.push(`节点 ${node.id} 的 sheet "${s.sheet_name || s.id}" 选了"历史运行"但未填 run_id`)
          if (!s.node_id) warnings.push(`节点 ${node.id} 的 sheet "${s.sheet_name || s.id}" 选了"历史运行"但未填 node_id`)
        } else {
          // node_output 模式：node_id 空时后端会用 depends_on 兜底
          if (noDeps && !s.node_id) {
            warnings.push(`节点 ${node.id} 没有 depends_on 且 sheet "${s.sheet_name || s.id}" 未选数据源 —— 跑出来会是空 sheet`)
          }
        }
      }
    }
  }
  return warnings
}

const saveWorkflow = async () => {
  const warnings = workflowDraftWarnings()
  if (warnings.length) {
    const ok = confirm(`保存前请确认：\n\n• ${warnings.join('\n• ')}\n\n继续保存？`)
    if (!ok) { setNotice('保存已取消'); return }
  }
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
  stopWorkflowAsyncPoll()
  workflowResult.value = null
  workflowAsyncJob.value = null
  workflowAsyncStatus.value = null
  try {
    const variables = parseVariables(workflowDraft.runtime_variables)
    workflowAsyncJob.value = await apiJson(`/api/workflows/${selectedWorkflowId.value}/run-async`, 'POST', { variables })
    workflowAsyncStatus.value = workflowAsyncJob.value
    setNotice(`后台执行已提交：${workflowAsyncJob.value.job_id?.slice(0, 8) || ''}`)
    workflowAsyncPollTimer.value = setInterval(async () => {
      try {
        workflowAsyncStatus.value = await apiGet(`/api/runs/${workflowAsyncJob.value.job_id}`)
        if (['success', 'failed', 'cancelled'].includes(workflowAsyncStatus.value.status)) {
          stopWorkflowAsyncPoll()
          if (workflowAsyncStatus.value.result) workflowResult.value = workflowAsyncStatus.value.result
          loadWorkflowRunHistory(selectedWorkflowId.value)
          loadAllWorkflowRuns()
          const statusText = workflowAsyncStatus.value.status === 'success' ? '完成' : workflowAsyncStatus.value.status === 'cancelled' ? '已取消' : '失败'
          setNotice(`后台执行${statusText}`)
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

// 历史 tab 行内"重跑"用：直接给 workflowId + variables 起一次后台执行，
// 不依赖 selectedWorkflowId 当前值，避免和正在编辑的 workflowDraft 串号。
// 起好后挂到 workflowAsyncJob/Status 复用现有 polling 链路；poll 完成时
// 仍然刷新 selectedWorkflowId 的 history 列表（同一个工作流的话就刚好）。
const runWorkflowAsyncWith = async (workflowId, variables = {}) => {
  if (!workflowId) return null
  stopWorkflowAsyncPoll()
  workflowResult.value = null
  workflowAsyncJob.value = null
  workflowAsyncStatus.value = null
  try {
    workflowAsyncJob.value = await apiJson(`/api/workflows/${workflowId}/run-async`, 'POST', { variables: variables || {} })
    workflowAsyncStatus.value = workflowAsyncJob.value
    setNotice('后台执行已提交')
    workflowAsyncPollTimer.value = setInterval(async () => {
      try {
        workflowAsyncStatus.value = await apiGet(`/api/runs/${workflowAsyncJob.value.job_id}`)
        if (['success', 'failed', 'cancelled'].includes(workflowAsyncStatus.value.status)) {
          stopWorkflowAsyncPoll()
          if (workflowAsyncStatus.value.result) workflowResult.value = workflowAsyncStatus.value.result
          if (selectedWorkflowId.value === workflowId) loadWorkflowRunHistory(workflowId)
          loadAllWorkflowRuns()
          const statusText = workflowAsyncStatus.value.status === 'success' ? '完成' : workflowAsyncStatus.value.status === 'cancelled' ? '已取消' : '失败'
          setNotice(`后台执行${statusText}`)
        }
      } catch (error) {
        stopWorkflowAsyncPoll()
        setNotice(`后台状态查询失败：${toErrorMessage(error)}`)
      }
    }, 1200)
    return workflowAsyncJob.value
  } catch (error) {
    setNotice(`后台执行提交失败：${toErrorMessage(error)}`)
    return null
  }
}

// 局部重跑：根据上次 run 的 run_id 和起点 node_id 起一次部分执行。
// 上游已 success 的节点会被复用 output，from_node 及其下游全部重跑。
// 起好后挂到 workflowAsyncJob/Status，跑完自动刷新历史。
const rerunWorkflowFromNode = async (runId, fromNodeId, variables = null) => {
  if (!runId || !fromNodeId) return null
  stopWorkflowAsyncPoll()
  workflowResult.value = null
  workflowAsyncJob.value = null
  workflowAsyncStatus.value = null
  try {
    const body = { from_node_id: fromNodeId }
    if (variables !== null) body.variables = variables
    workflowAsyncJob.value = await apiJson(`/api/workflow-runs/${runId}/rerun`, 'POST', body)
    workflowAsyncStatus.value = workflowAsyncJob.value
    setNotice(`已提交从 ${fromNodeId} 起的局部重跑`)
    workflowAsyncPollTimer.value = setInterval(async () => {
      try {
        workflowAsyncStatus.value = await apiGet(`/api/runs/${workflowAsyncJob.value.job_id}`)
        if (['success', 'failed', 'cancelled'].includes(workflowAsyncStatus.value.status)) {
          stopWorkflowAsyncPoll()
          if (workflowAsyncStatus.value.result) workflowResult.value = workflowAsyncStatus.value.result
          const wfId = workflowAsyncJob.value.workflow_id
          if (selectedWorkflowId.value === wfId) loadWorkflowRunHistory(wfId)
          loadAllWorkflowRuns()
          const statusText = workflowAsyncStatus.value.status === 'success' ? '完成' : workflowAsyncStatus.value.status === 'cancelled' ? '已取消' : '失败'
          setNotice(`局部重跑${statusText}`)
        }
      } catch (error) {
        stopWorkflowAsyncPoll()
        setNotice(`后台状态查询失败：${toErrorMessage(error)}`)
      }
    }, 1200)
    return workflowAsyncJob.value
  } catch (error) {
    setNotice(`局部重跑提交失败：${toErrorMessage(error)}`)
    return null
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

// startEdit/cancelEdit 直接走 store 方法（暴露在 provide('app')）
const startEditDatasource = datasourceStore.startEditDatasource
const cancelEditDatasource = datasourceStore.cancelEditDatasource

const updateDatasource = async (id) => {
  try {
    const updated = await apiJson(`/api/datasources/${id}`, 'PUT', editDraft.value)
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
  await apiJson('/api/datasources', 'POST', datasourceDraft.value)
  datasourceStore.resetDatasourceDraft()
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

// 含密码导出 —— 二次确认弹窗，避免误点把明文密码导出去。
const confirmIncludePasswords = (event) => {
  if (!confirm('导出文件将包含明文数据库密码。仅自用备份请确认；不要分享或提交到代码仓库。')) {
    event.preventDefault()
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
  let response
  try {
    response = await fetch('/history/export', { method: 'POST', body: form })
  } catch (error) {
    setNotice(`导出失败：${toErrorMessage(error)}`)
    return
  }
  if (!response.ok) {
    setNotice(`导出失败：${await response.text()}`)
    return
  }
  // 从 Content-Disposition 抽 filename；后端走 FastAPI FileResponse 会给。
  // 兜底：用时间戳避免文件名空。注意不能直接 window.open(blobUrl) —— 浏览器
  // 会按 inline 渲染 .xlsx 二进制成乱码，必须主动 <a download> 触发下载。
  const disposition = response.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  const filename = match ? decodeURIComponent(match[1]) : `history_export_${Date.now()}.xlsx`
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 让浏览器有机会启动下载，再回收 URL。延后些避免 Edge 偶发 download 中断。
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// 切路由时停掉所有轮询定时器，避免离开页面后还在打 API
watch(() => route.path, () => { stopAsyncPoll(); stopWorkflowAsyncPoll() })
onMounted(loadBootstrap)
onUnmounted(() => { stopAsyncPoll(); stopWorkflowAsyncPoll() })

// Shared context for view components. Reactive objects (state, lineage, batch, ...)
// preserve reactivity through inject; refs auto-unwrap in templates.
provide('app', {
  // navigation / shell state（views/activeView/activeViewLabel 由 vue-router 接管）
  loading, notice,
  // domain state
  state, taskDraft, datasourceDraft, editDraft, editingDatasourceId,
  selectedTaskId, currentTask, isSavedTask,
  taskValidationIssues, canSaveTask,
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
  runWorkflow, runWorkflowAsync, runWorkflowAsyncWith, rerunWorkflowFromNode, cancelWorkflowAsync,
  addWorkflowNode, removeWorkflowNode, moveWorkflowNode,
  addExportSheet, removeExportSheet, moveExportSheet, SHEET_TEMPLATES,
  addParameter, removeParameter,
  loadWorkflowRunHistory, loadWorkflowRunDetail, loadAllWorkflowRuns,
})
</script>

<template>
  <AppShell :loading="loading" @confirm-include-passwords="confirmIncludePasswords">
    <div class="px-6 py-6">
      <router-view />
    </div>
  </AppShell>
</template>
