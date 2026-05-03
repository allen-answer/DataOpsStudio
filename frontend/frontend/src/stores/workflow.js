/**
 * Workflow store —— 完整 workflow editor state + handlers + 节点编辑 helpers。
 *
 * 持有：
 *   - workflowDraft（编辑表单）+ selectedWorkflowId
 *   - 异步 job state（workflowAsyncJob/Status/PollTimer）
 *   - workflowRunHistory（当前 workflow 的 run summaries）
 *   - allWorkflowRuns（list 总览页用）
 *   - workflowResult（同步执行 / 历史详情 缓存）
 *   - workflowTemplates（codex 加的模板列表）
 *
 * Handlers 内部调 useNoticeStore + useBootstrapStore 协调，不依赖 App.vue。
 */
import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiGet, apiJson } from '../api'
import { useBootstrapStore } from './bootstrap'
import { useNoticeStore } from './notice'

function _toErrorMessage(error) {
  return error?.message || String(error || '未知错误')
}

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
  notifications: [],
  sensors: [],
  default_variables: '',
  runtime_variables: '',
  nodes: [],
})

const SHEET_TEMPLATES = {
  summary:        { sheet_name: '汇总对照',     dataset: 'summary' },
  diff:           { sheet_name: '差异明细',     dataset: 'diff' },
  only_source:    { sheet_name: '源端缺失',     dataset: 'only_source' },
  only_target:    { sheet_name: '目标端缺失',   dataset: 'only_target' },
  same:           { sheet_name: '一致行',       dataset: 'same' },
}

function _parseVariables(text) {
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

function _stringifyVariables(obj) {
  return Object.entries(obj || {}).map(([k, v]) => `${k}=${v}`).join('\n')
}


export const useWorkflowStore = defineStore('workflow', () => {
  const workflowDraft = reactive(DEFAULT_DRAFT())
  const selectedWorkflowId = ref('new')
  const workflowResult = ref(null)
  const workflowAsyncJob = ref(null)
  const workflowAsyncStatus = ref(null)
  const workflowAsyncPollTimer = ref(null)
  const workflowRunHistory = ref([])
  const allWorkflowRuns = ref([])
  const workflowTemplates = ref([])  // codex Phase: 模板列表

  const isSavedWorkflow = computed(() => selectedWorkflowId.value !== 'new')

  // currentWorkflow 依赖 bootstrap.state.workflows
  const currentWorkflow = computed(() => {
    const bootstrap = useBootstrapStore()
    return bootstrap.state.workflows.find((wf) => wf.id === selectedWorkflowId.value)
  })

  // ─── Draft helpers ────────────────────────────────────────────────────────

  function fillDraft(wf) {
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
    workflowDraft.notifications = Array.isArray(wf?.notifications)
      ? wf.notifications.map((item) => ({ ...item }))
      : []
    workflowDraft.sensors = Array.isArray(wf?.sensors)
      ? wf.sensors.map((item) => ({ ...item }))
      : []
    workflowDraft.default_variables = _stringifyVariables(wf?.default_variables)
    workflowDraft.runtime_variables = ''
    workflowDraft.nodes = (wf?.nodes || []).map((node) => ({
      id: node.id || '',
      type: node.type || 'compare',
      name: node.name || '',
      task_id: node.config?.task_id || '',
      source_sql_override: node.config?.source_sql_override || '',
      target_sql_override: node.config?.target_sql_override || '',
      sql: node.config?.sql || '',
      dialect: node.config?.dialect || '',
      input_mode: node.config?.input_mode || (node.config?.script_path
        ? (String(node.config?.script_filename || node.config?.script_path).toLowerCase().endsWith('.zip') ? 'uploaded_zip' : 'uploaded_file')
        : 'inline_sql'),
      script_path: node.config?.script_path || '',
      script_filename: node.config?.script_filename || '',
      script_kind: node.config?.script_kind || '',
      ai_enabled: Boolean(node.config?.ai_enabled),
      method: node.config?.method || 'GET',
      url: node.config?.url || '',
      body: node.config?.body || '',
      expect_status: node.config?.expect_status ?? '',
      // dataset 老 dot-path 反向归一化：'samples.diff' → 'diff'
      sheets: Array.isArray(node.config?.sheets)
        ? (() => {
            const deps = Array.isArray(node.depends_on) ? node.depends_on : []
            const singleDep = deps.length === 1 ? deps[0] : ''
            return node.config.sheets.map((s) => {
              let dataset = s.dataset || s.source_field || s.source || ''
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
      parameters: Array.isArray(node.config?.parameters)
        ? node.config.parameters.map((p) => ({ ...p }))
        : [],
      depends_on: Array.isArray(node.depends_on) ? [...node.depends_on] : [],
      when: node.when || '',
    }))
  }

  function buildNodeConfig(node) {
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
    if (node.type === 'lineage') {
      const mode = node.input_mode || 'inline_sql'
      const config = {
        input_mode: mode,
        ...(node.dialect ? { dialect: node.dialect } : {}),
      }
      if (node.ai_enabled) config.ai_enabled = true
      if (mode === 'inline_sql') {
        config.sql = node.sql || ''
      } else {
        config.script_path = node.script_path || ''
        config.script_filename = node.script_filename || ''
        config.script_kind = node.script_kind || (mode === 'uploaded_zip' ? 'zip' : 'file')
      }
      return config
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
        ...(s.source_type === 'history_run' && s.run_id ? { run_id: s.run_id } : {}),
        max_rows: Number(s.max_rows) || 100000,
      })),
    }
    return {}
  }

  function workflowPayload() {
    return {
      name: workflowDraft.name,
      description: workflowDraft.description || '',
      owner: workflowDraft.owner || '',
      tags: Array.isArray(workflowDraft.tags) ? workflowDraft.tags.filter(Boolean) : [],
      schedule_cron: workflowDraft.schedule_cron || '',
      project: workflowDraft.project || '',
      status: workflowDraft.status || 'draft',
      input_assets: (workflowDraft.input_assets || [])
        .filter((a) => a.key && a.key.trim())
        .map((a) => ({ key: a.key.trim(), kind: a.kind || 'table', description: a.description || '' })),
      output_assets: (workflowDraft.output_assets || [])
        .filter((a) => a.key && a.key.trim())
        .map((a) => ({ key: a.key.trim(), kind: a.kind || 'table', description: a.description || '' })),
      notifications: (workflowDraft.notifications || []).map((item) => ({ ...item })),
      sensors: (workflowDraft.sensors || []).map((item) => ({ ...item })),
      default_variables: _parseVariables(workflowDraft.default_variables),
      nodes: workflowDraft.nodes.map((node) => ({
        id: node.id,
        type: node.type,
        name: node.name,
        config: buildNodeConfig(node),
        depends_on: Array.isArray(node.depends_on) ? node.depends_on : [],
        when: node.when || '',
      })),
    }
  }

  function workflowDraftWarnings() {
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
            if (noDeps && !s.node_id) {
              warnings.push(`节点 ${node.id} 没有 depends_on 且 sheet "${s.sheet_name || s.id}" 未选数据源 —— 跑出来会是空 sheet`)
            }
          }
        }
      }
    }
    return warnings
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

  // ─── Node editor helpers ──────────────────────────────────────────────────

  function addWorkflowNode() {
    const nextIndex = workflowDraft.nodes.length + 1
    workflowDraft.nodes.push({
      id: `n${nextIndex}`, type: 'compare', name: '', depends_on: [], when: '',
      task_id: '', source_sql_override: '', target_sql_override: '',
      sql: '', dialect: '', input_mode: 'inline_sql', script_path: '', script_filename: '', script_kind: '',
      method: 'GET', url: '', body: '', expect_status: '',
      sheets: [
        { id: 'summary', enabled: true, sheet_name: '汇总对照', source_type: 'node_output', node_id: '', dataset: 'summary', run_id: '', max_rows: 100000 },
        { id: 'diff',    enabled: true, sheet_name: '差异明细', source_type: 'node_output', node_id: '', dataset: 'diff',    run_id: '', max_rows: 100000 },
      ],
      parameters: [],
    })
  }

  function removeWorkflowNode(index) {
    workflowDraft.nodes.splice(index, 1)
  }

  function moveWorkflowNode(index, delta) {
    const target = index + delta
    if (target < 0 || target >= workflowDraft.nodes.length) return
    const tmp = workflowDraft.nodes[index]
    workflowDraft.nodes[index] = workflowDraft.nodes[target]
    workflowDraft.nodes[target] = tmp
  }

  function addExportSheet(node, templateId) {
    const tmpl = SHEET_TEMPLATES[templateId] || SHEET_TEMPLATES.summary
    const id = `${templateId}_${(node.sheets || []).length + 1}`
    ;(node.sheets || (node.sheets = [])).push({
      id, enabled: true, sheet_name: tmpl.sheet_name,
      source_type: 'node_output', node_id: '', dataset: tmpl.dataset,
      run_id: '', max_rows: 100000,
    })
  }

  function removeExportSheet(node, sheetIdx) {
    if (Array.isArray(node.sheets)) node.sheets.splice(sheetIdx, 1)
  }

  function moveExportSheet(node, sheetIdx, delta) {
    const sheets = node.sheets
    if (!Array.isArray(sheets)) return
    const target = sheetIdx + delta
    if (target < 0 || target >= sheets.length) return
    const tmp = sheets[sheetIdx]
    sheets[sheetIdx] = sheets[target]
    sheets[target] = tmp
  }

  function addParameter(node, type = 'fixed') {
    ;(node.parameters || (node.parameters = [])).push({
      name: '', type,
      default: type === 'multi_value' ? [] : '',
      source: type === 'relative_date' ? 'yesterday' : '',
      required: true, description: '', sql: '', datasource: '',
    })
  }

  function removeParameter(node, idx) {
    if (Array.isArray(node.parameters)) node.parameters.splice(idx, 1)
  }

  // ─── Run history loaders ──────────────────────────────────────────────────

  async function loadWorkflowRunHistory(workflowId) {
    try {
      workflowRunHistory.value = await apiGet(`/api/workflows/${workflowId}/runs?limit=20`)
    } catch (error) {
      workflowRunHistory.value = []
    }
  }

  async function loadAllWorkflowRuns() {
    try {
      allWorkflowRuns.value = await apiGet('/api/workflow-runs?limit=200')
    } catch (error) {
      allWorkflowRuns.value = []
    }
  }

  async function loadWorkflowRunDetail(runId) {
    const notice = useNoticeStore()
    try {
      const detail = await apiGet(`/api/workflow-runs/${runId}`)
      workflowResult.value = detail
      workflowAsyncStatus.value = null
      workflowAsyncJob.value = null
      notice.setNotice(`已加载历史运行 ${runId.slice(0, 8)}`)
    } catch (error) {
      notice.setNotice(`加载失败：${_toErrorMessage(error)}`)
    }
  }

  async function reemitWorkflowRunOpenLineage(runId) {
    const notice = useNoticeStore()
    if (!runId) return null
    try {
      const payload = await apiJson(`/api/workflow-runs/${runId}/openlineage/emit`, 'POST', {})
      if (workflowResult.value?.run_id === runId) {
        workflowResult.value.integrations = workflowResult.value.integrations || {}
        workflowResult.value.integrations.openlineage = payload.results || []
      }
      notice.setNotice(payload.ok ? 'OpenLineage 已重发' : 'OpenLineage 重发完成，但存在失败项')
      return payload
    } catch (error) {
      notice.setNotice(`OpenLineage 重发失败：${_toErrorMessage(error)}`)
      return null
    }
  }

  async function loadWorkflowTemplates() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    try {
      workflowTemplates.value = await apiGet('/api/workflow-templates')
      bootstrap.state.workflowTemplates = workflowTemplates.value
    } catch (error) {
      notice.setNotice(`加载模板失败：${_toErrorMessage(error)}`)
    }
  }

  async function saveWorkflowAsTemplate() {
    const notice = useNoticeStore()
    if (selectedWorkflowId.value === 'new') {
      notice.setNotice('请先保存作业流，再沉淀为模板')
      return null
    }
    const defaultName = `${workflowDraft.name || currentWorkflow.value?.name || 'workflow'} 模板`
    const name = prompt('模板名称', defaultName)
    if (!name) return null
    try {
      const template = await apiJson(`/api/workflows/${selectedWorkflowId.value}/template`, 'POST', {
        name,
        description: workflowDraft.description || currentWorkflow.value?.description || '',
        category: workflowDraft.project || currentWorkflow.value?.project || '',
        tags: Array.isArray(workflowDraft.tags) ? workflowDraft.tags : [],
      })
      const bootstrap = useBootstrapStore()
      workflowTemplates.value = [template, ...workflowTemplates.value.filter((item) => item.id !== template.id)]
      bootstrap.state.workflowTemplates = workflowTemplates.value
      notice.setNotice('作业流模板已保存')
      return template
    } catch (error) {
      notice.setNotice(`保存模板失败：${_toErrorMessage(error)}`)
      return null
    }
  }

  async function createWorkflowFromTemplate(templateId, options = {}) {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    const template = workflowTemplates.value.find((item) => item.id === templateId)
    const defaultName = options.name || `${template?.workflow?.name || template?.name || 'workflow'} 副本`
    const name = options.skipPrompt ? defaultName : prompt('新作业流名称', defaultName)
    if (!name) return null
    try {
      const workflow = await apiJson(`/api/workflow-templates/${templateId}/instantiate`, 'POST', {
        name,
        project: options.project || '',
        owner: options.owner || '',
        status: options.status || 'draft',
      })
      bootstrap.state.workflows.unshift(workflow)
      selectWorkflow(workflow.id)
      notice.setNotice('已从模板创建作业流')
      return workflow
    } catch (error) {
      notice.setNotice(`从模板创建失败：${_toErrorMessage(error)}`)
      return null
    }
  }

  async function deleteWorkflowTemplate(templateId) {
    const notice = useNoticeStore()
    if (!confirm('确认删除这个作业流模板？不会影响已创建的作业流。')) return
    try {
      await apiJson(`/api/workflow-templates/${templateId}`, 'DELETE')
      const bootstrap = useBootstrapStore()
      workflowTemplates.value = workflowTemplates.value.filter((item) => item.id !== templateId)
      bootstrap.state.workflowTemplates = workflowTemplates.value
      notice.setNotice('模板已删除')
    } catch (error) {
      notice.setNotice(`删除模板失败：${_toErrorMessage(error)}`)
    }
  }

  // ─── CRUD ─────────────────────────────────────────────────────────────────

  function selectWorkflow(id) {
    const bootstrap = useBootstrapStore()
    stopWorkflowAsyncPoll()
    selectedWorkflowId.value = id
    workflowResult.value = null
    workflowAsyncJob.value = null
    workflowAsyncStatus.value = null
    workflowRunHistory.value = []
    fillDraft(id === 'new' ? null : bootstrap.state.workflows.find((wf) => wf.id === id))
    if (id !== 'new') loadWorkflowRunHistory(id)
  }

  async function saveWorkflow() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    const warnings = workflowDraftWarnings()
    if (warnings.length) {
      const ok = confirm(`保存前请确认：\n\n• ${warnings.join('\n• ')}\n\n继续保存？`)
      if (!ok) { notice.setNotice('保存已取消'); return }
    }
    notice.setNotice('保存中...')
    try {
      if (selectedWorkflowId.value === 'new') {
        const created = await apiJson('/api/workflows', 'POST', workflowPayload())
        bootstrap.state.workflows.unshift(created)
        selectWorkflow(created.id)
      } else {
        const updated = await apiJson(`/api/workflows/${selectedWorkflowId.value}`, 'PUT', workflowPayload())
        const idx = bootstrap.state.workflows.findIndex((wf) => wf.id === selectedWorkflowId.value)
        if (idx !== -1) bootstrap.state.workflows[idx] = updated
      }
      notice.setNotice('作业流已保存')
    } catch (error) {
      notice.setNotice(`保存失败：${_toErrorMessage(error)}`)
    }
  }

  async function deleteWorkflow() {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    if (selectedWorkflowId.value === 'new') return
    try {
      await apiJson(`/api/workflows/${selectedWorkflowId.value}`, 'DELETE')
      bootstrap.state.workflows = bootstrap.state.workflows.filter((wf) => wf.id !== selectedWorkflowId.value)
      selectWorkflow(bootstrap.state.workflows[0]?.id || 'new')
      notice.setNotice('作业流已删除')
    } catch (error) {
      notice.setNotice(`删除失败：${_toErrorMessage(error)}`)
    }
  }

  // ─── Run / cancel ─────────────────────────────────────────────────────────

  async function runWorkflow() {
    const notice = useNoticeStore()
    if (selectedWorkflowId.value === 'new') return
    workflowResult.value = null
    workflowAsyncJob.value = null
    workflowAsyncStatus.value = null
    notice.setNotice('执行中...')
    try {
      const variables = _parseVariables(workflowDraft.runtime_variables)
      workflowResult.value = await apiJson(`/api/workflows/${selectedWorkflowId.value}/run`, 'POST', { variables })
      notice.setNotice(`执行${workflowResult.value.status === 'success' ? '完成' : '失败'}`)
      loadWorkflowRunHistory(selectedWorkflowId.value)
    } catch (error) {
      notice.setNotice(`执行失败：${_toErrorMessage(error)}`)
    }
  }

  // 抽出公共 polling 链路：runAsync / runAsyncWith / rerunFromNode 共享
  function _attachAsyncPoll(onTerminal) {
    workflowAsyncPollTimer.value = setInterval(async () => {
      const notice = useNoticeStore()
      try {
        workflowAsyncStatus.value = await apiGet(`/api/runs/${workflowAsyncJob.value.job_id}`)
        if (['success', 'failed', 'cancelled'].includes(workflowAsyncStatus.value.status)) {
          stopWorkflowAsyncPoll()
          if (workflowAsyncStatus.value.result) workflowResult.value = workflowAsyncStatus.value.result
          if (typeof onTerminal === 'function') onTerminal()
          loadAllWorkflowRuns()
          const statusText = workflowAsyncStatus.value.status === 'success' ? '完成'
            : workflowAsyncStatus.value.status === 'cancelled' ? '已取消' : '失败'
          notice.setNotice(`后台执行${statusText}`)
        }
      } catch (error) {
        stopWorkflowAsyncPoll()
        notice.setNotice(`后台状态查询失败：${_toErrorMessage(error)}`)
      }
    }, 1200)
  }

  async function runWorkflowAsync() {
    const notice = useNoticeStore()
    if (selectedWorkflowId.value === 'new') return
    stopWorkflowAsyncPoll()
    workflowResult.value = null
    workflowAsyncJob.value = null
    workflowAsyncStatus.value = null
    try {
      const variables = _parseVariables(workflowDraft.runtime_variables)
      workflowAsyncJob.value = await apiJson(`/api/workflows/${selectedWorkflowId.value}/run-async`, 'POST', { variables })
      workflowAsyncStatus.value = workflowAsyncJob.value
      notice.setNotice(`后台执行已提交：${workflowAsyncJob.value.job_id?.slice(0, 8) || ''}`)
      const wfId = selectedWorkflowId.value
      _attachAsyncPoll(() => loadWorkflowRunHistory(wfId))
    } catch (error) {
      notice.setNotice(`后台执行提交失败：${_toErrorMessage(error)}`)
    }
  }

  // 历史 tab 行内"重跑"用：直接给 workflowId + variables 起一次后台执行，
  // 不依赖 selectedWorkflowId 当前值
  async function runWorkflowAsyncWith(workflowId, variables = {}) {
    const notice = useNoticeStore()
    if (!workflowId) return null
    stopWorkflowAsyncPoll()
    workflowResult.value = null
    workflowAsyncJob.value = null
    workflowAsyncStatus.value = null
    try {
      workflowAsyncJob.value = await apiJson(`/api/workflows/${workflowId}/run-async`, 'POST', { variables: variables || {} })
      workflowAsyncStatus.value = workflowAsyncJob.value
      notice.setNotice('后台执行已提交')
      _attachAsyncPoll(() => {
        if (selectedWorkflowId.value === workflowId) loadWorkflowRunHistory(workflowId)
      })
      return workflowAsyncJob.value
    } catch (error) {
      notice.setNotice(`后台执行提交失败：${_toErrorMessage(error)}`)
      return null
    }
  }

  // 局部重跑：上游已 success 节点复用 output，from_node 及其下游全部重跑
  async function rerunWorkflowFromNode(runId, fromNodeId, variables = null) {
    const notice = useNoticeStore()
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
      notice.setNotice(`已提交从 ${fromNodeId} 起的局部重跑`)
      _attachAsyncPoll(() => {
        const wfId = workflowAsyncJob.value?.workflow_id
        if (wfId && selectedWorkflowId.value === wfId) loadWorkflowRunHistory(wfId)
      })
      return workflowAsyncJob.value
    } catch (error) {
      notice.setNotice(`局部重跑提交失败：${_toErrorMessage(error)}`)
      return null
    }
  }

  async function cancelWorkflowAsync() {
    const notice = useNoticeStore()
    if (!workflowAsyncJob.value) return
    try {
      workflowAsyncStatus.value = await apiJson(`/api/runs/${workflowAsyncJob.value.job_id}/cancel`, 'POST')
    } catch (error) {
      notice.setNotice(`取消失败：${_toErrorMessage(error)}`)
    }
  }

  return {
    // state
    workflowDraft, selectedWorkflowId,
    workflowResult, workflowAsyncJob, workflowAsyncStatus, workflowAsyncPollTimer,
    workflowRunHistory, allWorkflowRuns, workflowTemplates,
    // computed
    isSavedWorkflow, currentWorkflow,
    // draft helpers
    fillDraft, buildNodeConfig, workflowPayload, workflowDraftWarnings,
    resetWorkflowState, stopWorkflowAsyncPoll,
    // node editor
    addWorkflowNode, removeWorkflowNode, moveWorkflowNode,
    addExportSheet, removeExportSheet, moveExportSheet,
    addParameter, removeParameter,
    SHEET_TEMPLATES,
    // run history
    loadWorkflowRunHistory, loadAllWorkflowRuns, loadWorkflowRunDetail, reemitWorkflowRunOpenLineage,
    loadWorkflowTemplates, saveWorkflowAsTemplate, createWorkflowFromTemplate, deleteWorkflowTemplate,
    // CRUD + run handlers
    selectWorkflow, saveWorkflow, deleteWorkflow,
    runWorkflow, runWorkflowAsync, runWorkflowAsyncWith,
    rerunWorkflowFromNode, cancelWorkflowAsync,
  }
})
