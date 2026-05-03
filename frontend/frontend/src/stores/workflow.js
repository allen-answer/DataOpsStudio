/**
 * Workflow store —— Pinia 化第四步。
 *
 * 持有 workflow editor 自己的 state：
 *   - workflowDraft（编辑表单）
 *   - selectedWorkflowId / 异步 job state
 *   - workflowRunHistory（当前 workflow 的 run summaries）
 *   - allWorkflowRuns（list 总览页用）
 *   - workflowResult（同步执行结果）
 *
 * 不持有：
 *   - state.workflows 列表 —— 仍由 App.vue 顶层 reactive `state` 持有
 *
 * Handler（saveWorkflow / runWorkflow / cancelWorkflow / rerunWorkflowFromNode 等）
 * 仍在 App.vue —— 它们依赖 loadBootstrap / state.workflows / setNotice 等跨 store 协调。
 */
import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'


const DEFAULT_DRAFT = () => ({
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


export const useWorkflowStore = defineStore('workflow', () => {
  const workflowDraft = reactive(DEFAULT_DRAFT())
  const selectedWorkflowId = ref('new')
  const workflowResult = ref(null)
  const workflowAsyncJob = ref(null)
  const workflowAsyncStatus = ref(null)
  const workflowAsyncPollTimer = ref(null)
  const workflowRunHistory = ref([])    // current workflow's run summaries
  const allWorkflowRuns = ref([])       // all runs across workflows (list overview)

  const isSavedWorkflow = computed(() => selectedWorkflowId.value !== 'new')

  function fillDraft(workflow) {
    Object.assign(workflowDraft, {
      ...DEFAULT_DRAFT(),
      ...(workflow || {}),
      tags: workflow?.tags ? [...workflow.tags] : [],
      input_assets: workflow?.input_assets ? workflow.input_assets.map(a => ({ ...a })) : [],
      output_assets: workflow?.output_assets ? workflow.output_assets.map(a => ({ ...a })) : [],
      nodes: workflow?.nodes ? workflow.nodes.map(n => JSON.parse(JSON.stringify(n))) : [],
      default_variables: workflow?.default_variables || '',
      runtime_variables: workflow?.runtime_variables || '',
    })
  }

  function resetWorkflowState() {
    workflowResult.value = null
    workflowAsyncStatus.value = null
    workflowRunHistory.value = []
  }

  function stopWorkflowAsyncPoll() {
    if (workflowAsyncPollTimer.value) {
      clearInterval(workflowAsyncPollTimer.value)
      workflowAsyncPollTimer.value = null
    }
  }

  return {
    workflowDraft, selectedWorkflowId,
    workflowResult, workflowAsyncJob, workflowAsyncStatus, workflowAsyncPollTimer,
    workflowRunHistory, allWorkflowRuns,
    isSavedWorkflow,
    fillDraft, resetWorkflowState, stopWorkflowAsyncPoll,
  }
})
