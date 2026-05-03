/**
 * Compare task store —— Pinia 化第三步。
 *
 * 持有"task workbench 自己的"那一块状态：
 *   - taskDraft（保存按钮前的表单）
 *   - selectedTaskId / 异步执行状态（asyncJob / asyncStatus）
 *   - 字段选择器（sourceFields / targetFields → fieldPickerRows）
 *   - 单次执行结果 + preview cache
 *
 * 不持有：
 *   - state.tasks（task 列表）、state.datasources —— 仍由 App.vue 顶层 state
 *     reactive 持有。本轮先把"工作台 state"抽出，list 来源后续轮次再迁。
 *
 * Handler（saveTask / runTask / selectTask 等）暂留在 App.vue —— 它们依赖
 * loadBootstrap / state.tasks，搬过来要把整个 bootstrap 流也迁。这一轮只做
 * "drop 状态 + utility"，handler 调 store 暴露的 fillDraft / taskPayload。
 */
import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { validateTaskDraft } from '../utils/taskValidation'


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
  numeric_tolerance: '',
  trim_strings: false,
  case_insensitive: false,
  empty_as_null: false,
  max_rows: 100000,
  export_max_rows: 50000,
  fetch_chunk_size: 5000,
  stream_compare: false,
})


export const useTaskStore = defineStore('task', () => {
  // ─── State ────────────────────────────────────────────────────────────────
  const taskDraft = reactive(DEFAULT_DRAFT())
  const selectedTaskId = ref('new')

  const sourceFields = ref([])
  const targetFields = ref([])

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
    }
  }

  function resetSelectionState() {
    previewOutput.value = ''
    sourcePreviewData.value = null
    targetPreviewData.value = null
    sourceFields.value = []
    targetFields.value = []
    compareResult.value = null
    asyncStatus.value = null
  }

  function stopAsyncPoll() {
    if (asyncPollTimer.value) {
      clearInterval(asyncPollTimer.value)
      asyncPollTimer.value = null
    }
  }

  return {
    // state
    taskDraft, selectedTaskId,
    sourceFields, targetFields,
    previewOutput, sourcePreviewData, targetPreviewData,
    compareResult, asyncJob, asyncStatus, asyncPollTimer,
    // computed
    ignoredColumnSet, fieldPickerRows, fieldPickerHasFields,
    taskValidation, taskValidationIssues, canSaveTask, isSavedTask,
    // utility
    normalizeColumn, parseCsv, parseMappings,
    // actions
    toggleFieldIncluded, fieldPickerSelectAll, fieldPickerExcludeOneSided,
    fillDraft, taskPayload, resetSelectionState, stopAsyncPoll,
  }
})
