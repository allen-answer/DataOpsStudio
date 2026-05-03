<script setup>
import { computed, onMounted, onUnmounted, provide, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useClipboard } from '@vueuse/core'
import { storeToRefs } from 'pinia'
import { apiGet } from './api'
import AppShell from './layouts/AppShell.vue'
// validateTaskDraft 已经迁到 useTaskStore 内部使用
import { useNoticeStore } from './stores/notice'
import { useDatasourceStore } from './stores/datasource'
import { useTaskStore } from './stores/task'
import { useWorkflowStore } from './stores/workflow'
import { useLineageStore } from './stores/lineage'
import { useBatchStore } from './stores/batch'
import { useHistoryStore } from './stores/history'
import { useBootstrapStore } from './stores/bootstrap'
import { useAuthStore } from './stores/auth'
import { useProjectStore } from './stores/project'

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
const workflowStore = useWorkflowStore()
const lineageStore = useLineageStore()
const batchStore = useBatchStore()
const historyStore = useHistoryStore()
const bootstrapStore = useBootstrapStore()
const authStore = useAuthStore()
const projectStore = useProjectStore()
const { state } = bootstrapStore
const { notice } = storeToRefs(noticeStore)
const { actionStatus, setNotice, setActionStatus } = noticeStore
const { editingDatasourceId } = storeToRefs(datasourceStore)
const { datasourceDraft, editDraft } = datasourceStore
// task store 解构：ref 字段经 storeToRefs；reactive (taskDraft) 直接拿
const {
  selectedTaskId, sourceFields, targetFields,
  sourceFieldWarnings, targetFieldWarnings,
  previewOutput, sourcePreviewData, targetPreviewData,
  compareResult, asyncJob, asyncStatus, asyncPollTimer,
  ignoredColumnSet, fieldPickerRows, fieldPickerHasFields,
  taskValidation, taskValidationIssues, canSaveTask, isSavedTask,
  schemaDiagnostics,
} = storeToRefs(taskStore)
const { taskDraft } = taskStore
const {
  toggleFieldIncluded, fieldPickerSelectAll, fieldPickerExcludeOneSided,
  normalizeColumn, parseCsv, parseMappings,
  fillDraft, taskPayload, resetSelectionState, stopAsyncPoll,
  selectTask, saveTask, deleteTask, copyTask,
  runTask, runAsync, cancelAsync, previewTask,
  extractFields, recommendKey, formatSql, uploadExcel,
} = taskStore

// workflow store 解构：ref 字段经 storeToRefs，reactive (workflowDraft) 直接拿
const {
  selectedWorkflowId, workflowResult,
  workflowAsyncJob, workflowAsyncStatus, workflowAsyncPollTimer,
  workflowRunHistory, allWorkflowRuns, workflowTemplates,
  isSavedWorkflow, currentWorkflow,
} = storeToRefs(workflowStore)
const { workflowDraft, SHEET_TEMPLATES } = workflowStore
const {
  fillDraft: fillWorkflowDraft,
  buildNodeConfig, workflowPayload, workflowDraftWarnings,
  resetWorkflowState, stopWorkflowAsyncPoll,
  addWorkflowNode, removeWorkflowNode, moveWorkflowNode,
  addExportSheet, removeExportSheet, moveExportSheet,
  addParameter, removeParameter,
  loadWorkflowRunHistory, loadAllWorkflowRuns, loadWorkflowRunDetail, reemitWorkflowRunOpenLineage,
  loadWorkflowTemplates, saveWorkflowAsTemplate, createWorkflowFromTemplate, deleteWorkflowTemplate,
  selectWorkflow, saveWorkflow, deleteWorkflow,
  runWorkflow, runWorkflowAsync, runWorkflowAsyncWith,
  rerunWorkflowFromNode, cancelWorkflowAsync,
} = workflowStore

// lineage / batch store
const { lineageAIStatus } = storeToRefs(lineageStore)
const { lineage } = lineageStore
const { batchActiveTab, batchSelectedFileNames } = storeToRefs(batchStore)
const { batch, batchTabs } = batchStore
// history store —— 选择 + tab + 任务过滤
const {
  selectedHistory, selectedSheets, selectedHistoryTaskId, historyActiveTab,
} = storeToRefs(historyStore)

const route = useRoute()
const loading = ref(false)
// selectedWorkflowId / workflow* 迁到 useWorkflowStore；batchActiveTab 迁到 useBatchStore；
// selectedHistoryTaskId / historyActiveTab / selectedHistory / selectedSheets 迁到 useHistoryStore

// state 已迁到 useBootstrapStore（顶部 `const { state } = bootstrapStore` 解构）

// taskDraft / selectedTaskId / 字段选择 / preview / asyncJob 已迁移到 useTaskStore（顶部解构）

// datasourceDraft / editDraft / editingDatasourceId 已迁移到 useDatasourceStore
// （顶部 storeToRefs 暴露），下面的 startEdit/cancelEdit 也走 store。

// workflowDraft / lineage / batch reactive 已迁到对应 store（顶部解构）

// currentTask 仍依赖 state.tasks（list 还在 App.vue），留在这里 join；
// isSavedTask / taskValidation / taskValidationIssues / canSaveTask 已迁到 useTaskStore。
// currentTask / currentWorkflow 已迁到对应 store
const { currentTask } = storeToRefs(taskStore)
// selectedHistory / selectedSheets 已迁到 useHistoryStore
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
// batchSelectedFileNames / batchTabs 已迁到 useBatchStore
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
    // 数据拉取 + 写入 state 的逻辑已迁到 useBootstrapStore.reload()
    await bootstrapStore.reload()
    workflowTemplates.value = state.workflowTemplates || []
    // 联动业务（默认 db_type / 默认数据源 / 任务选择）仍在 App.vue：
    // 这部分跨 store（datasource / task / bootstrap）协调，先在 App.vue 收口
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

// setActionStatus / setNotice 迁到 useNoticeStore；stopAsyncPoll 迁到 useTaskStore（顶部解构）。

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

// deleteHistory / exportHistory 已迁到 useHistoryStore
const deleteHistory = historyStore.deleteHistory
const exportHistory = historyStore.exportHistory

// 所有 task handlers 已迁到 useTaskStore（顶部解构）：
//   selectTask / saveTask / deleteTask / copyTask / uploadExcel
//   runTask / runAsync / cancelAsync / previewTask
//   extractFields / recommendKey / formatSql
//   currentTask / fillDraft / taskPayload

// Workflow handlers（含模板）已迁到 useWorkflowStore。

// startEdit/cancelEdit 直接走 store 方法（暴露在 provide('app')）
const startEditDatasource = datasourceStore.startEditDatasource
const cancelEditDatasource = datasourceStore.cancelEditDatasource

// datasource CRUD handlers 已迁到 useDatasourceStore（顶部解构）。createDatasource
// 不再触发 loadBootstrap → 修复了"创建数据源会跳转到第一个任务"的副作用。
const createDatasource = datasourceStore.createDatasource
const updateDatasource = datasourceStore.updateDatasource
const deleteDatasource = datasourceStore.deleteDatasource
const testDatasource = datasourceStore.testDatasource

// analyzeLineage / analyzeBatch 已迁到对应 store
const analyzeLineage = lineageStore.analyzeLineage
const loadLineageAIStatus = lineageStore.loadLineageAIStatus
const analyzeBatch = batchStore.analyzeBatch

// 含密码导出 —— 二次确认弹窗，避免误点把明文密码导出去。
const confirmIncludePasswords = (event) => {
  if (!confirm('导出文件将包含明文数据库密码。仅自用备份请确认；不要分享或提交到代码仓库。')) {
    event.preventDefault()
  }
}

// exportHistory 迁到 useHistoryStore（顶部已挂同名 alias）

// 切路由时停掉所有轮询定时器，避免离开页面后还在打 API
watch(() => route.path, () => { stopAsyncPoll(); stopWorkflowAsyncPoll() })
// 已登录才拉 bootstrap 数据 + refresh user；未登录走 LoginView 全屏（router 守卫已跳）
onMounted(async () => {
  if (!authStore.isLoggedIn) return
  await authStore.refreshMe()  // 验证 token + 刷新 user 信息（过期则 401 自动跳 login）
  if (authStore.isLoggedIn) {
    await projectStore.reload()  // 项目列表先就位 —— sidebar dropdown / bootstrap 过滤都靠它
    await loadBootstrap()
  }
})
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
  sourceFieldWarnings, targetFieldWarnings,
  compareResult, asyncJob, asyncStatus, previewOutput, actionStatus,
  lineage, lineageAIStatus, batch, batchActiveTab, batchTabs,
  selectedHistory, selectedSheets, selectedHistoryTaskId, historyActiveTab,
  // computed
  driverItems, historyTaskOptions, filteredHistory,
  compareHistoryCount, lineageHistoryCount, compareBuckets,
  batchSelectedFileNames,
  fieldPickerRows, fieldPickerHasFields, schemaDiagnostics,
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
  analyzeLineage, analyzeBatch, loadLineageAIStatus,
  // handlers — history
  loadHistory, deleteHistory, exportHistory,
  // workflow state + handlers
  workflowDraft, selectedWorkflowId, currentWorkflow, isSavedWorkflow,
  workflowResult, workflowAsyncJob, workflowAsyncStatus, workflowRunHistory, allWorkflowRuns, workflowTemplates,
  selectWorkflow, saveWorkflow, deleteWorkflow,
  loadWorkflowTemplates, saveWorkflowAsTemplate, createWorkflowFromTemplate, deleteWorkflowTemplate,
  runWorkflow, runWorkflowAsync, runWorkflowAsyncWith, rerunWorkflowFromNode, cancelWorkflowAsync,
  addWorkflowNode, removeWorkflowNode, moveWorkflowNode,
  addExportSheet, removeExportSheet, moveExportSheet, SHEET_TEMPLATES,
  addParameter, removeParameter,
  loadWorkflowRunHistory, loadWorkflowRunDetail, loadAllWorkflowRuns, reemitWorkflowRunOpenLineage,
})
</script>

<template>
  <!-- 登录页全屏渲染，不套 sidebar / topbar 壳 -->
  <router-view v-if="route.meta.public" />
  <AppShell v-else :loading="loading" @confirm-include-passwords="confirmIncludePasswords">
    <div class="px-6 py-6">
      <router-view />
    </div>
  </AppShell>
</template>
