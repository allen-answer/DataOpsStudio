/**
 * Compare task store —— task workbench 完整状态 + handlers。
 *
 * 状态：
 *   - taskDraft / selectedTaskId / 字段选择器 / preview cache / 异步 job
 *   - currentTask / isSavedTask / taskValidation / canSaveTask computed
 *
 * Handlers（saveTask / runTask / runAsync / etc.）：
 *   - 跨 store 通过 useNoticeStore + useBootstrapStore 协调
 *   - apiJson/apiGet/apiForm 在 store 内 import
 *   - 不再依赖 App.vue 注入的回调
 */
import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiForm, apiGet, apiJson } from '../api'
import { validateTaskDraft } from '../utils/taskValidation'
import { useBootstrapStore } from './bootstrap'
import { useNoticeStore } from './notice'
import { useProjectStore } from './project'

function _toErrorMessage(error) {
  return error?.message || String(error || '未知错误')
}

// 主键名称启发式：xxx_id / xxx_no / xxx_code / xxx_key 优先；id / pk / no 也算
const ID_LIKE_RE = /(^|_)(id|pk|key|no|code|sn|uuid|guid)$/i
function _pickKeyCandidates(fields) {
  return fields.filter(c => ID_LIKE_RE.test(c))
}


const DEFAULT_DRAFT = () => ({
  name: '',
  source_kind: 'sql',
  target_kind: 'sql',
  source_id: '',
  target_id: '',
  sql_mode: 'single',
  source_sql: '',
  target_sql: '',
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
  // CSV / Parquet 通用文件字段
  source_file_path: '',
  source_file_filename: '',
  source_file_encoding: 'utf-8-sig',
  source_csv_delimiter: ',',
  target_file_path: '',
  target_file_filename: '',
  target_file_encoding: 'utf-8-sig',
  target_csv_delimiter: ',',
  key_columns: '',
  ignore_columns: '',
  column_mappings: '',
  schema_policy: 'warn',
  numeric_tolerance: '',
  trim_strings: false,
  case_insensitive: false,
  empty_as_null: false,
  max_rows: 100000,
  export_max_rows: 50000,
  fetch_chunk_size: 5000,
  stream_compare: false,
  project_id: '',
})


export const useTaskStore = defineStore('task', () => {
  // ─── State ────────────────────────────────────────────────────────────────
  const taskDraft = reactive(DEFAULT_DRAFT())
  const selectedTaskId = ref('new')

  const sourceFields = ref([])
  const targetFields = ref([])
  const sourceFieldWarnings = ref([])
  const targetFieldWarnings = ref([])

  const previewOutput = ref('')
  const sourcePreviewData = ref(null)
  const targetPreviewData = ref(null)

  const compareResult = ref(null)
  const asyncJob = ref(null)
  const asyncStatus = ref(null)
  const asyncPollTimer = ref(null)

  // ─── Utility ──────────────────────────────────────────────────────────────
  const normalizeColumn = (col) => String(col || '').trim().toLowerCase()
  const parseCsv = (value) => String(value || '').split(',').map((item) => item.trim()).filter(Boolean)
  const parseMappings = (value) => {
    const result = {}
    String(value || '').split('\n').forEach((line) => {
      const [source, target] = line.split('->').map((item) => item?.trim())
      if (source && target) result[source] = target
    })
    return result
  }

  // ─── Field picker ─────────────────────────────────────────────────────────
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

  const schemaDiagnostics = computed(() => {
    const sourceSet = new Set(sourceFields.value.map(normalizeColumn))
    const targetSet = new Set(targetFields.value.map(normalizeColumn))
    const sourceOnly = sourceFields.value.filter(c => !targetSet.has(normalizeColumn(c)))
    const targetOnly = targetFields.value.filter(c => !sourceSet.has(normalizeColumn(c)))
    const warnings = [
      ...sourceFieldWarnings.value,
      ...targetFieldWarnings.value,
    ]
    if (sourceFields.value.length && targetFields.value.length && sourceFields.value.length !== targetFields.value.length) {
      warnings.push({
        type: 'schema_count_mismatch',
        level: 'warning',
        message: `源/目标字段数量不一致：source=${sourceFields.value.length}，target=${targetFields.value.length}；未配置字段映射时会按位置映射到较短一侧。`,
      })
    }
    if (sourceOnly.length || targetOnly.length) {
      warnings.push({
        type: 'one_sided_columns',
        level: 'warning',
        message: `仅源 ${sourceOnly.length} 个，仅目标 ${targetOnly.length} 个；多出的字段会按缺失值参与对比，或可加入忽略字段。`,
        source_only: sourceOnly,
        target_only: targetOnly,
      })
    }
    return { sourceOnly, targetOnly, warnings }
  })

  function toggleFieldIncluded(name) {
    const key = normalizeColumn(name)
    const currentIgnore = parseCsv(taskDraft.ignore_columns)
    const without = currentIgnore.filter((col) => normalizeColumn(col) !== key)
    if (without.length < currentIgnore.length) {
      taskDraft.ignore_columns = without.join(', ')
    } else {
      taskDraft.ignore_columns = [...currentIgnore, name].join(', ')
    }
  }

  function fieldPickerSelectAll() {
    taskDraft.ignore_columns = ''
  }

  function fieldPickerExcludeOneSided() {
    const oneSided = fieldPickerRows.value
      .filter((row) => !(row.onSource && row.onTarget))
      .map((row) => row.name)
    taskDraft.ignore_columns = oneSided.join(', ')
  }

  // ─── Validation（前端即时反馈，后端权威） ───────────────────────────────────
  const taskValidation = computed(() => validateTaskDraft(taskDraft))
  const taskValidationIssues = computed(() => taskValidation.value.issues)
  const canSaveTask = computed(() => taskValidation.value.canSave)

  // ─── Saved-task helper ────────────────────────────────────────────────────
  const isSavedTask = computed(() => selectedTaskId.value !== 'new')

  // ─── Draft helpers ────────────────────────────────────────────────────────
  function fillDraft(task, fallbackDatasourceId = '') {
    Object.assign(taskDraft, {
      ...DEFAULT_DRAFT(),
      name: task?.name || '',
      source_kind: task?.source_kind || 'sql',
      target_kind: task?.target_kind || 'sql',
      source_id: task?.source_id || fallbackDatasourceId,
      target_id: task?.target_id || fallbackDatasourceId,
      sql_mode: task?.sql_mode || 'single',
      source_sql: task?.source_sql || '',
      target_sql: task?.target_sql || '',
      source_excel_path: task?.source_excel_path || '',
      source_excel_filename: task?.source_excel_path ? task.source_excel_path.split('/').pop() : '',
      source_sheet: task?.source_sheet || '',
      source_header_row: task?.source_header_row || 1,
      target_excel_path: task?.target_excel_path || '',
      target_excel_filename: task?.target_excel_path ? task.target_excel_path.split('/').pop() : '',
      target_sheet: task?.target_sheet || '',
      target_header_row: task?.target_header_row || 1,
      source_file_path: task?.source_file_path || '',
      source_file_filename: task?.source_file_path ? task.source_file_path.split('/').pop() : '',
      source_file_encoding: task?.source_file_encoding || 'utf-8-sig',
      source_csv_delimiter: task?.source_csv_delimiter || ',',
      target_file_path: task?.target_file_path || '',
      target_file_filename: task?.target_file_path ? task.target_file_path.split('/').pop() : '',
      target_file_encoding: task?.target_file_encoding || 'utf-8-sig',
      target_csv_delimiter: task?.target_csv_delimiter || ',',
      key_columns: (task?.key_columns || []).join(', '),
      ignore_columns: (task?.rules?.ignore_columns || []).join(', '),
      column_mappings: Object.entries(task?.rules?.column_mappings || {}).map(([s, t]) => `${s} -> ${t}`).join('\n'),
      schema_policy: task?.rules?.schema_policy || 'warn',
      numeric_tolerance: task?.rules?.numeric_tolerance ?? '',
      trim_strings: Boolean(task?.rules?.trim_strings),
      case_insensitive: Boolean(task?.rules?.case_insensitive),
      empty_as_null: Boolean(task?.rules?.empty_as_null),
      max_rows: task?.limits?.max_rows || 100000,
      export_max_rows: task?.limits?.export_max_rows || 50000,
      fetch_chunk_size: task?.limits?.fetch_chunk_size || 5000,
      stream_compare: Boolean(task?.limits?.stream_compare),
      project_id: task?.project_id || '',
    })
  }

  function taskPayload() {
    return {
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
      source_file_path: taskDraft.source_file_path,
      source_file_encoding: taskDraft.source_file_encoding,
      source_csv_delimiter: taskDraft.source_csv_delimiter,
      target_file_path: taskDraft.target_file_path,
      target_file_encoding: taskDraft.target_file_encoding,
      target_csv_delimiter: taskDraft.target_csv_delimiter,
      key_columns: parseCsv(taskDraft.key_columns),
      rules: {
        ignore_columns: parseCsv(taskDraft.ignore_columns),
        column_mappings: parseMappings(taskDraft.column_mappings),
        schema_policy: taskDraft.schema_policy || 'warn',
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
      // 当前项目（taskDraft 已有 project_id 时优先用 draft 值，编辑场景保留原值）
      project_id: taskDraft.project_id ?? useProjectStore().currentProjectId ?? '',
    }
  }

  function resetSelectionState() {
    previewOutput.value = ''
    sourcePreviewData.value = null
    targetPreviewData.value = null
    sourceFields.value = []
    targetFields.value = []
    sourceFieldWarnings.value = []
    targetFieldWarnings.value = []
    compareResult.value = null
    asyncStatus.value = null
  }

  function stopAsyncPoll() {
    if (asyncPollTimer.value) {
      clearInterval(asyncPollTimer.value)
      asyncPollTimer.value = null
    }
  }

  // currentTask 依赖 bootstrap.state.tasks
  const currentTask = computed(() => {
    const bootstrap = useBootstrapStore()
    return bootstrap.state.tasks.find((task) => task.id === selectedTaskId.value)
  })

  // ─── Handlers（跨 store 协调） ───────────────────────────────────────────────

  function selectTask(id) {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    stopAsyncPoll()
    selectedTaskId.value = id
    resetSelectionState()
    notice.setActionStatus(
      id === 'new' ? 'idle' : 'ready',
      id === 'new' ? '新建任务' : '任务已载入',
      id === 'new' ? '填写任务信息后点击保存任务。' : '可以执行、预览、后台执行或复制当前任务。',
    )
    const datasourcesFallback = bootstrap.state.datasources[0]?.id || ''
    const targetTask = id === 'new' ? null : bootstrap.state.tasks.find((task) => task.id === id)
    fillDraft(targetTask, datasourcesFallback)
  }

  async function saveTask() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    if (!canSaveTask.value) {
      const errs = taskValidationIssues.value.filter(i => i.level === 'error')
      const head = errs[0]?.message || '配置不完整'
      const more = errs.length > 1 ? `（共 ${errs.length} 项问题）` : ''
      notice.setNotice(`保存被拦截：${head}${more}`)
      notice.setActionStatus('error', '保存被拦截', `${head}${more}`)
      return
    }
    notice.setNotice('保存中...')
    notice.setActionStatus('running', '正在保存任务', '正在校验数据源、SQL 和主键配置。')
    try {
      if (selectedTaskId.value === 'new') {
        const created = await apiJson('/api/tasks', 'POST', taskPayload())
        bootstrap.state.tasks.unshift(created)
        selectTask(created.id)
      } else {
        const updated = await apiJson(`/api/tasks/${selectedTaskId.value}`, 'PUT', taskPayload())
        const index = bootstrap.state.tasks.findIndex((task) => task.id === selectedTaskId.value)
        if (index !== -1) bootstrap.state.tasks[index] = updated
      }
      notice.setNotice('任务已保存')
      notice.setActionStatus('success', '任务已保存', '现在可以执行对比、预览或后台执行。')
    } catch (error) {
      notice.setNotice('保存失败')
      previewOutput.value = _toErrorMessage(error)
      notice.setActionStatus('error', '保存失败', _toErrorMessage(error))
    }
  }

  async function deleteTask() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    if (selectedTaskId.value === 'new') return
    notice.setActionStatus('running', '正在删除任务')
    try {
      await apiJson(`/api/tasks/${selectedTaskId.value}`, 'DELETE')
      bootstrap.state.tasks = bootstrap.state.tasks.filter((task) => task.id !== selectedTaskId.value)
      selectTask(bootstrap.state.tasks[0]?.id || 'new')
      notice.setActionStatus('success', '任务已删除')
    } catch (error) {
      previewOutput.value = _toErrorMessage(error)
      notice.setActionStatus('error', '删除失败', _toErrorMessage(error))
    }
  }

  async function copyTask() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    if (selectedTaskId.value === 'new') return
    notice.setActionStatus('running', '正在复制任务', currentTask.value?.name || '')
    try {
      const copied = await apiJson(`/api/tasks/${selectedTaskId.value}/copy`, 'POST')
      bootstrap.state.tasks.unshift(copied)
      selectTask(copied.id)
      notice.setActionStatus('success', '任务已复制', `已创建：${copied.name}`)
    } catch (error) {
      previewOutput.value = _toErrorMessage(error)
      notice.setActionStatus('error', '复制失败', _toErrorMessage(error))
    }
  }

  async function uploadExcel(side, file) {
    const notice = useNoticeStore()
    if (!file) return
    notice.setActionStatus('running', `上传 ${side === 'source' ? '源' : '目标'} Excel`, file.name)
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
      notice.setActionStatus('success', 'Excel 已上传', `${response.filename} · ${response.sheets.length} 个 sheet`)
    } catch (error) {
      notice.setActionStatus('error', 'Excel 上传失败', _toErrorMessage(error))
    }
  }

  async function runTask() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    if (selectedTaskId.value === 'new') return
    asyncStatus.value = null
    previewOutput.value = '执行中...'
    notice.setActionStatus('running', '正在执行对比', '查询源/目标数据并生成 JSON、Excel 结果。')
    try {
      const result = await apiJson(`/api/tasks/${selectedTaskId.value}/run`, 'POST')
      compareResult.value = result
      previewOutput.value = JSON.stringify(result, null, 2)
      const s = result.summary || {}
      notice.setActionStatus(
        'success', '对比完成',
        `only_source=${s.only_source ?? 0}，only_target=${s.only_target ?? 0}，diff=${s.diff ?? 0}，same=${s.same ?? 0}`,
      )
      // 历史列表刷新（保留当前 task 选择）
      await bootstrap.reload()
    } catch (error) {
      previewOutput.value = _toErrorMessage(error)
      notice.setActionStatus('error', '执行失败', _toErrorMessage(error))
    }
  }

  async function runAsync() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    if (selectedTaskId.value === 'new') return
    previewOutput.value = ''
    notice.setActionStatus('running', '正在提交后台任务')
    try {
      asyncJob.value = await apiJson(`/api/tasks/${selectedTaskId.value}/run-async`, 'POST')
      asyncStatus.value = asyncJob.value
      notice.setActionStatus('running', '后台任务已提交', `任务号：${asyncJob.value.job_id}`)
      asyncPollTimer.value = setInterval(async () => {
        try {
          asyncStatus.value = await apiGet(`/api/runs/${asyncJob.value.job_id}`)
          if (asyncStatus.value.status === 'success' && asyncStatus.value.result) {
            compareResult.value = asyncStatus.value.result
          }
          const terminal = ['success', 'failed', 'cancelled'].includes(asyncStatus.value.status)
          notice.setActionStatus(
            terminal ? (asyncStatus.value.status === 'success' ? 'success' : 'error') : 'running',
            `后台任务状态：${asyncStatus.value.status}`,
            asyncStatus.value.error || `阶段：${asyncStatus.value.stage || '运行中'}`,
          )
          if (terminal) {
            stopAsyncPoll()
            await bootstrap.reload()
          }
        } catch (error) {
          stopAsyncPoll()
          notice.setActionStatus('error', '后台状态查询失败', _toErrorMessage(error))
        }
      }, 1200)
    } catch (error) {
      asyncStatus.value = null
      notice.setActionStatus('error', '后台任务提交失败', _toErrorMessage(error))
    }
  }

  async function cancelAsync() {
    const notice = useNoticeStore()
    if (!asyncJob.value) return
    notice.setActionStatus('running', '正在取消后台任务')
    try {
      asyncStatus.value = await apiJson(`/api/runs/${asyncJob.value.job_id}/cancel`, 'POST')
      notice.setActionStatus('success', '已发送取消请求', '后台任务会在当前安全阶段结束后停止。')
    } catch (error) {
      notice.setActionStatus('error', '取消失败', _toErrorMessage(error))
    }
  }

  async function previewTask(side) {
    const notice = useNoticeStore()
    const previewRef = side === 'source' ? sourcePreviewData : targetPreviewData
    previewRef.value = { loading: true }
    const kind = taskDraft[`${side}_kind`] || 'sql'
    const sql = side === 'target' && taskDraft.sql_mode === 'double' ? taskDraft.target_sql : taskDraft.source_sql
    const datasourceId = side === 'target' ? taskDraft.target_id : taskDraft.source_id
    const payload = { side, kind, limit: 10 }
    if (kind === 'sql') {
      payload.sql = sql
      payload.datasource_id = datasourceId
    } else if (kind === 'excel') {
      payload.excel_path = taskDraft[`${side}_excel_path`]
      payload.sheet = taskDraft[`${side}_sheet`]
      payload.header_row = Number(taskDraft[`${side}_header_row`]) || 1
    } else if (kind === 'csv') {
      payload.file_path = taskDraft[`${side}_file_path`]
      payload.encoding = taskDraft[`${side}_file_encoding`] || 'utf-8-sig'
      payload.delimiter = taskDraft[`${side}_csv_delimiter`] || ','
      payload.header_row = Number(taskDraft[`${side}_header_row`]) || 1
    } else if (kind === 'parquet') {
      payload.file_path = taskDraft[`${side}_file_path`]
    }
    notice.setActionStatus('running', side === 'target' ? '正在预览目标数据' : '正在预览源数据')
    try {
      const result = await apiJson('/api/preview/rows', 'POST', payload)
      previewRef.value = result
      const columns = result.columns?.length ? result.columns : Object.keys(result.rows?.[0] || {})
      if (columns.length) {
        if (side === 'source') {
          sourceFields.value = columns
          sourceFieldWarnings.value = result.warnings || []
        } else {
          targetFields.value = columns
          targetFieldWarnings.value = result.warnings || []
        }
      }
      notice.setActionStatus('success', '预览完成', `返回 ${result.rows?.length ?? 0} 行`)
    } catch (error) {
      previewRef.value = { error: _toErrorMessage(error) }
      notice.setActionStatus('error', '预览失败', _toErrorMessage(error))
    }
  }

  async function extractFields(side) {
    const notice = useNoticeStore()
    const kind = taskDraft[`${side}_kind`] || 'sql'
    notice.setActionStatus('running', '正在提取字段')
    try {
      let columns = []
      let warnings = []
      if (kind === 'excel') {
        const result = await apiJson('/api/preview/columns', 'POST', {
          kind: 'excel',
          excel_path: taskDraft[`${side}_excel_path`],
          sheet: taskDraft[`${side}_sheet`],
          header_row: Number(taskDraft[`${side}_header_row`]) || 1,
        })
        columns = result.columns || []
        warnings = result.warnings || []
      } else {
        const sql = side === 'target' && taskDraft.sql_mode === 'single' ? taskDraft.source_sql : taskDraft[`${side}_sql`]
        const data = await apiJson('/api/sql/assist', 'POST', { sql, dialect: '' })
        columns = (data.output_columns || []).filter(c => !c.includes('*'))
        if (columns.length === 0) {
          notice.setActionStatus('running', 'SELECT * 检测到，正在查询数据库获取字段...')
          const result = await apiJson('/api/preview/columns', 'POST', {
            kind: 'sql',
            datasource_id: side === 'source' ? taskDraft.source_id : taskDraft.target_id,
            sql,
          })
          columns = result.columns || []
          warnings = result.warnings || []
        }
      }
      if (side === 'source') sourceFields.value = columns
      else targetFields.value = columns
      if (side === 'source') sourceFieldWarnings.value = warnings
      else targetFieldWarnings.value = warnings
      notice.setActionStatus('success', '字段提取完成', `识别字段 ${columns.length} 个`)
    } catch (error) {
      notice.setActionStatus('error', '字段提取失败', _toErrorMessage(error))
    }
  }

  async function recommendKey() {
    const notice = useNoticeStore()
    notice.setActionStatus('running', '正在推荐主键')
    try {
      const sourceSet = new Set(sourceFields.value.map(normalizeColumn))
      const targetSet = new Set(targetFields.value.map(normalizeColumn))
      if (sourceSet.size && targetSet.size) {
        const intersect = sourceFields.value.filter(c => targetSet.has(normalizeColumn(c)))
        const candidates = _pickKeyCandidates(intersect)
        if (candidates.length) {
          taskDraft.key_columns = candidates.join(', ')
          notice.setActionStatus('success', '主键推荐完成', `从两侧交集挑选：${candidates.join(', ')}`)
          return
        }
        if (intersect.length) {
          taskDraft.key_columns = intersect[0]
          notice.setActionStatus('warning', '主键候选有限',
            `两侧字段交集中未识别到 id 类命名；已临时填入 ${intersect[0]}，请确认后修改`)
          return
        }
        notice.setActionStatus('error', '无法推荐主键',
          '源字段与目标字段没有交集；先在「字段映射」做列对齐，或检查两侧 schema')
        return
      }
      // 退回旧路径：source 是 SQL 时让 sqlglot 抽
      if (taskDraft.source_kind === 'sql' && taskDraft.source_sql) {
        const data = await apiJson('/api/sql/assist', 'POST', { sql: taskDraft.source_sql, dialect: '' })
        if (data.key_candidates?.length) taskDraft.key_columns = data.key_candidates.join(', ')
        notice.setActionStatus('success', '主键推荐完成', `推荐：${data.key_candidates?.join(', ') || '无候选'}`)
        return
      }
      if (sourceFields.value.length) {
        const candidates = _pickKeyCandidates(sourceFields.value)
        if (candidates.length) {
          taskDraft.key_columns = candidates.join(', ')
          notice.setActionStatus('success', '主键推荐完成', `从源字段挑选：${candidates.join(', ')}`)
          return
        }
      }
      notice.setActionStatus('error', '无法推荐主键',
        '请先在「数据来源」点击两侧的「提取字段」加载列名，或手动填主键')
    } catch (error) {
      notice.setActionStatus('error', '主键推荐失败', _toErrorMessage(error))
    }
  }

  async function formatSql(side) {
    const notice = useNoticeStore()
    const sql = side === 'source' ? taskDraft.source_sql : taskDraft.target_sql
    notice.setActionStatus('running', '正在格式化 SQL')
    try {
      const data = await apiJson('/api/sql/assist', 'POST', { sql, dialect: '' })
      if (side === 'source') {
        taskDraft.source_sql = data.formatted_sql || taskDraft.source_sql
      } else {
        taskDraft.target_sql = data.formatted_sql || taskDraft.target_sql
      }
      previewOutput.value = JSON.stringify(data, null, 2)
      notice.setActionStatus('success', 'SQL 已格式化')
    } catch (error) {
      previewOutput.value = _toErrorMessage(error)
      notice.setActionStatus('error', 'SQL 格式化失败', _toErrorMessage(error))
    }
  }

  return {
    // state
    taskDraft, selectedTaskId,
    sourceFields, targetFields,
    sourceFieldWarnings, targetFieldWarnings,
    previewOutput, sourcePreviewData, targetPreviewData,
    compareResult, asyncJob, asyncStatus, asyncPollTimer,
    // computed
    ignoredColumnSet, fieldPickerRows, fieldPickerHasFields,
    taskValidation, taskValidationIssues, canSaveTask, isSavedTask,
    currentTask,
    schemaDiagnostics,
    // utility
    normalizeColumn, parseCsv, parseMappings,
    // field picker actions
    toggleFieldIncluded, fieldPickerSelectAll, fieldPickerExcludeOneSided,
    // draft helpers
    fillDraft, taskPayload, resetSelectionState, stopAsyncPoll,
    // workbench handlers
    selectTask, saveTask, deleteTask, copyTask,
    runTask, runAsync, cancelAsync, previewTask,
    extractFields, recommendKey, formatSql,
    uploadExcel,
  }
})
