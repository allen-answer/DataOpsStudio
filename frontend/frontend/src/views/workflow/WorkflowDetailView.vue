<script setup>
import { computed, defineAsyncComponent, inject, ref, watch } from 'vue'
import { healthMeta, workflowHealth, synthesizeEvents, resolveAllParameters } from '../../mock/workflow_meta'
import WorkflowCompareNodeEditor     from '../../components/workflow/WorkflowCompareNodeEditor.vue'
import WorkflowParamsNodeEditor      from '../../components/workflow/WorkflowParamsNodeEditor.vue'
import WorkflowLineageNodeEditor     from '../../components/workflow/WorkflowLineageNodeEditor.vue'
import WorkflowExcelExportNodeEditor from '../../components/workflow/WorkflowExcelExportNodeEditor.vue'
import WorkflowHistoryPanel          from '../../components/workflow/WorkflowHistoryPanel.vue'
import WorkflowDagCanvas             from '../../components/workflow/WorkflowDagCanvas.vue'
import WorkflowSettingsPanel         from '../../components/workflow/WorkflowSettingsPanel.vue'

const SqlEditor = defineAsyncComponent(() => import('../../components/SqlEditor.vue'))

const emit = defineEmits(['back', 'open-run'])

// 注：节点编辑器（params / compare / lineage / excel_export）已抽成子组件，
// 它们各自从 inject('app') 拿需要的全局方法（addParameter / addExportSheet
// 等），所以这里不再解构那些 helper。父组件保留 addWorkflowNode 等还在用的。
const {
  state, workflowDraft, selectedWorkflowId, currentWorkflow, isSavedWorkflow,
  workflowResult, workflowAsyncJob, workflowAsyncStatus, workflowRunHistory,
  allWorkflowRuns, loadAllWorkflowRuns,
  saveWorkflow, deleteWorkflow,
  runWorkflow, runWorkflowAsync, runWorkflowAsyncWith, cancelWorkflowAsync,
  addWorkflowNode, removeWorkflowNode, moveWorkflowNode,
  loadWorkflowRunDetail,
} = inject('app')

// 注：运行历史 tab 整段（行展开 + mini gantt + 状态徽章 + 复用变量重跑）
// 已抽到 components/workflow/WorkflowHistoryPanel.vue，保留 selectedNodeId 等
// 用在 DAG canvas / 节点详情的 ref 在下面。

const activeTab = ref('history')   // history / events / lineage / config
const selectedNodeId = ref('')

// 新建态：自动落到「节点配置」tab，引导用户开始编辑
watch(selectedWorkflowId, (id) => {
  if (id === 'new') activeTab.value = 'config'
}, { immediate: true })

// 运行参数：从作业流里所有 type=params 节点的 config.parameters 收集。
// 没有 params 节点 → 空列表，UI 显示"还没有参数定义"占位。
const realParameters = computed(() => {
  const out = []
  for (const node of workflowDraft.nodes || []) {
    if (node.type !== 'params') continue
    for (const p of node.parameters || []) {
      if (p?.name) out.push(p)
    }
  }
  return out
})
const displayParameters = computed(() => realParameters.value)

// 参数解析：把每个参数定义解析成下次运行将使用的具体值（预览用）。
const resolvedParams = computed(() => resolveAllParameters(displayParameters.value))

// 最近一次 run（用于 DAG canvas 上叠加节点状态）
const latestRun = computed(() => workflowResult.value || null)
// health 兜底：workflowHealth 可能返回 healthMeta 里没有的 key（旧数据 / 边界
// 状态），直接 healthMeta[health].pill 会炸渲染。统一走 healthDisplay 取兜底值。
const health = computed(() => workflowHealth(currentWorkflow.value, workflowRunHistory.value[0] || null) || 'none')
const healthDisplay = computed(() => healthMeta[health.value] || healthMeta.none)

const otherNodeIds = (currentId) => workflowDraft.nodes.map((n) => n.id).filter((id) => id && id !== currentId)

// 空态点 "+ 添加" 时跳到节点配置 tab 并新增第一个节点
const handleAddNodeFromCanvas = () => {
  activeTab.value = 'config'
  addWorkflowNode()
}

const tabs = [
  { id: 'history',  label: '运行历史' },
  { id: 'events',   label: '事件日志' },
  { id: 'lineage',  label: '依赖关系' },
  { id: 'config',   label: '节点配置' },
]

// 合成事件流（来自最近一次运行）
const recentEvents = computed(() => synthesizeEvents(latestRun.value))
const eventTypeMeta = {
  RUN_START:    { glyph: '▶', text: 'text-blue-600',    label: '运行开始' },
  RUN_SUCCESS:  { glyph: '✓', text: 'text-emerald-600', label: '运行成功' },
  RUN_FAILURE:  { glyph: '✕', text: 'text-rose-600',    label: '运行失败' },
  STEP_START:   { glyph: '·', text: 'text-slate-500',   label: '步骤开始' },
  STEP_SUCCESS: { glyph: '✓', text: 'text-emerald-600', label: '步骤完成' },
  STEP_FAILURE: { glyph: '✕', text: 'text-rose-600',    label: '步骤失败' },
  STEP_SKIPPED: { glyph: '⊘', text: 'text-slate-500',   label: '步骤跳过' },
}
const levelClass = (level) => ({ INFO: 'text-slate-700', WARN: 'text-amber-700', ERROR: 'text-rose-700' }[level] || 'text-slate-700')

watch(selectedWorkflowId, () => { selectedNodeId.value = '' })
</script>

<template>
  <!-- 必须二选一才进详情：要么是新建态（selectedWorkflowId='new'），要么有真实选中的
       currentWorkflow。否则下面的 currentWorkflow.xxx 会炸渲染（'new' 时 currentWorkflow
       是 undefined，但 currentWorkflow?.xxx 也只是不渲染，下面新建态的 input 仍能显示）。
       注意区分两种空状态：
       - 已经在用、但还没选作业流 → 引导去总览选
       - 选了但作业流被删 / id 错 → 同样引导去总览
       两个 case 文案合并成一条。 -->
  <div v-if="selectedWorkflowId !== 'new' && !currentWorkflow" class="rounded-xl border border-dashed border-slate-200 bg-white p-12 text-center text-sm text-slate-400">
    请先从「作业流总览」中选择一个作业流，或点击右上角「新建作业流」
  </div>

  <div v-else class="flex flex-col gap-3">
    <!-- 顶部 header：名称 + 健康度 + 调度 + 操作 -->
    <div class="rounded-xl border border-slate-200 bg-white shadow-sm">
      <div class="flex items-start justify-between gap-3 px-4 py-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-[11px] text-slate-500">
            <button class="text-slate-600 transition hover:text-blue-600" @click="emit('back')">← 作业流总览</button>
            <span v-if="selectedWorkflowId !== 'new'" class="text-slate-300">/</span>
            <span v-if="selectedWorkflowId !== 'new'" class="font-mono">{{ selectedWorkflowId.slice(0, 8) }}</span>
          </div>
          <div class="mt-1.5 flex flex-wrap items-center gap-2.5">
            <input v-if="selectedWorkflowId === 'new'"
                   v-model="workflowDraft.name"
                   placeholder="新建作业流名称..."
                   class="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xl font-bold text-slate-800 focus:border-blue-400 focus:outline-none">
            <h1 v-else class="text-xl font-bold text-slate-800">{{ currentWorkflow?.name }}</h1>
            <span v-if="selectedWorkflowId === 'new'" class="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700 ring-1 ring-inset ring-blue-200">
              <span class="h-1.5 w-1.5 rounded-full bg-blue-500"></span>
              草稿（未保存）
            </span>
            <template v-else>
              <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="healthDisplay.pill">
                <span class="h-1.5 w-1.5 rounded-full" :class="healthDisplay.dot"></span>
                {{ healthDisplay.label }}
              </span>
              <span v-if="latestRun" class="font-mono text-[11.5px] text-slate-500">
                最近运行：{{ latestRun.started_at?.slice(5) }} · {{ latestRun.elapsed_seconds }}s
              </span>
              <span v-if="workflowDraft.schedule_cron" class="font-mono text-[11.5px] text-slate-500" title="cron 表达式（仅展示，外部调度器读取）">
                ⏱ {{ workflowDraft.schedule_cron }}
              </span>
              <span v-else class="text-[11px] text-slate-400">手动触发</span>
              <span class="text-slate-300">·</span>
              <span class="text-[11.5px] text-slate-500">
                负责人 <span class="font-medium text-slate-700">{{ workflowDraft.owner || '—' }}</span>
              </span>
            </template>
          </div>
          <div v-if="selectedWorkflowId !== 'new'" class="mt-1.5 flex flex-wrap items-center gap-1">
            <span v-for="tag in workflowDraft.tags" :key="tag" class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ tag }}</span>
            <span v-if="!workflowDraft.tags?.length" class="text-[10.5px] text-slate-300">无标签</span>
          </div>
          <p v-else class="mt-1.5 text-[11.5px] text-slate-500">填写名称、在「节点配置」中添加节点，然后保存。保存后即可执行。</p>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!isSavedWorkflow" :title="!isSavedWorkflow ? '请先保存作业流' : '立即运行'" @click="runWorkflow">
            <span>▶</span> 立即运行
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!isSavedWorkflow" :title="!isSavedWorkflow ? '请先保存作业流' : '提交后台执行'" @click="runWorkflowAsync">
            后台执行
          </button>
          <button v-if="workflowAsyncJob && workflowAsyncStatus && !['success','failed','cancelled'].includes(workflowAsyncStatus.status)"
                  class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-2.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100"
                  @click="cancelWorkflowAsync">
            ▣ 取消
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50" @click="saveWorkflow">
            保存
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-rose-700 transition hover:border-rose-200 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!isSavedWorkflow" @click="deleteWorkflow">
            删除
          </button>
        </div>
      </div>
    </div>

    <!-- 主区域：左 DAG canvas + 右元数据 -->
    <div class="grid grid-cols-[minmax(0,1fr)_320px] gap-3">
      <WorkflowDagCanvas
        :nodes="workflowDraft.nodes"
        :latest-run="latestRun"
        v-model:selected-node-id="selectedNodeId"
        @add-node="handleAddNodeFromCanvas" />

      <WorkflowSettingsPanel
        :workflow-draft="workflowDraft"
        :parameters="displayParameters"
        :resolved-params="resolvedParams" />
    </div>

    <!-- 标签页区域 -->
    <div class="rounded-xl border border-slate-200 bg-white shadow-sm">
      <nav class="flex border-b border-slate-200 px-2 text-[12px]">
        <button v-for="t in tabs" :key="t.id"
                class="border-b-2 px-3 py-2 font-semibold transition"
                :class="activeTab === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'"
                @click="activeTab = t.id">
          {{ t.label }}
          <span v-if="t.id === 'history'" class="ml-1 rounded bg-slate-100 px-1 text-[10px] font-mono text-slate-500">{{ workflowRunHistory.length }}</span>
        </button>
      </nav>

      <!-- 运行历史 tab：抽到 WorkflowHistoryPanel；'open-run' 透传到父 view，
           父再 emit 给 WorkflowView 切到运行详情页 -->
      <WorkflowHistoryPanel v-if="activeTab === 'history'" @open-run="(rid) => emit('open-run', rid)" />

      <!-- 事件日志（基于最近一次运行合成） -->
      <div v-if="activeTab === 'events'" class="px-3 py-2">
        <div v-if="!recentEvents.length" class="py-8 text-center text-[12.5px] text-slate-400">
          还没有运行记录 — 跑过一次后，事件日志才会有内容
        </div>
        <div v-else class="font-mono text-[12px]">
          <div v-for="(ev, idx) in recentEvents" :key="idx" class="grid grid-cols-[140px_140px_1fr] items-start gap-3 border-b border-slate-100 px-2 py-1.5 last:border-0">
            <span class="text-[10.5px] text-slate-400">{{ ev.ts }}</span>
            <span class="flex items-center gap-1.5">
              <span class="grid h-4 w-4 shrink-0 place-items-center text-[11px]" :class="eventTypeMeta[ev.type]?.text || 'text-slate-500'">{{ eventTypeMeta[ev.type]?.glyph || '·' }}</span>
              <span class="text-[10.5px] font-bold uppercase tracking-wider" :class="eventTypeMeta[ev.type]?.text || 'text-slate-500'">{{ eventTypeMeta[ev.type]?.label || ev.type }}</span>
            </span>
            <div class="min-w-0">
              <p class="break-words" :class="levelClass(ev.level)">{{ ev.msg }}</p>
              <p v-if="ev.step" class="mt-0.5 text-[10.5px] text-slate-400">step={{ ev.step }}</p>
              <div v-if="ev.metadata" class="mt-1 flex flex-wrap gap-1">
                <span v-for="(v, k) in ev.metadata" :key="k" class="rounded bg-slate-100 px-1.5 py-0.5 text-[10.5px]"><span class="text-slate-500">{{ k }}=</span><span class="text-slate-700">{{ v }}</span></span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 依赖关系（输入 / 输出资产） -->
      <div v-else-if="activeTab === 'lineage'" class="grid grid-cols-2 gap-4 p-4">
        <div>
          <h3 class="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">输入资产 ({{ workflowDraft.input_assets.length }})</h3>
          <ul v-if="workflowDraft.input_assets.length" class="space-y-2">
            <li v-for="(asset, i) in workflowDraft.input_assets" :key="i" class="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
              <span class="h-2 w-2 rounded-full bg-slate-400"></span>
              <div class="min-w-0 flex-1">
                <p class="truncate font-mono text-[12.5px] font-semibold text-slate-800">{{ asset.key }}</p>
                <p v-if="asset.description" class="text-[11px] text-slate-500">{{ asset.description }}</p>
              </div>
              <span class="rounded bg-white px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ asset.kind }}</span>
            </li>
          </ul>
          <p v-else class="text-[12px] text-slate-400">还没有声明输入资产 — 在「基础设置」面板下方添加</p>
        </div>
        <div>
          <h3 class="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">输出资产 ({{ workflowDraft.output_assets.length }})</h3>
          <ul v-if="workflowDraft.output_assets.length" class="space-y-2">
            <li v-for="(asset, i) in workflowDraft.output_assets" :key="i" class="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
              <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
              <div class="min-w-0 flex-1">
                <p class="truncate font-mono text-[12.5px] font-semibold text-slate-800">{{ asset.key }}</p>
                <p v-if="asset.description" class="text-[11px] text-slate-500">{{ asset.description }}</p>
              </div>
              <span class="rounded bg-white px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ asset.kind }}</span>
            </li>
          </ul>
          <p v-else class="text-[12px] text-slate-400">还没有声明输出资产 — 在「基础设置」面板下方添加</p>
        </div>
      </div>

      <!-- 节点配置（保留原有编辑器） -->
      <div v-else-if="activeTab === 'config'" class="space-y-4 p-4">
        <div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <label>
            <span class="mb-2 block text-[10px] font-bold uppercase tracking-wider text-slate-400">作业流名称</span>
            <input v-model="workflowDraft.name" class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-blue-400 focus:outline-none">
          </label>
          <label>
            <span class="mb-2 block text-[10px] font-bold uppercase tracking-wider text-slate-400">默认变量（每行 key=value）</span>
            <textarea v-model="workflowDraft.default_variables" class="min-h-[60px] w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-[12.5px] focus:border-blue-400 focus:outline-none" placeholder="biz_date=2026-05-01"></textarea>
          </label>
        </div>

        <div class="rounded-xl border border-slate-200 bg-slate-50/40 p-3">
          <div class="mb-3 flex items-center justify-between">
            <h3 class="text-sm font-bold text-slate-700">节点 ({{ workflowDraft.nodes.length }})</h3>
            <button class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-blue-700" @click="addWorkflowNode">+ 新增节点</button>
          </div>
          <div v-if="!workflowDraft.nodes.length" class="rounded-lg border border-dashed border-slate-200 bg-white p-6 text-center text-sm text-slate-400">还没有节点 — 点右上角「+ 新增节点」开始（推荐 params 起步，再接 compare/lineage/http）</div>
          <div v-else class="space-y-2">
            <div v-for="(node, index) in workflowDraft.nodes" :key="index" class="rounded-lg border border-slate-200 bg-white p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <div class="flex items-center gap-2">
                  <span class="grid h-6 w-6 place-items-center rounded bg-blue-600 text-[10px] font-bold text-white">{{ index + 1 }}</span>
                  <input v-model="node.id" class="w-24 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-mono">
                  <span class="rounded px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase"
                        :class="{
                          'bg-sky-50 text-sky-700': node.type === 'params',
                          'bg-blue-50 text-blue-700': node.type === 'compare',
                          'bg-emerald-50 text-emerald-700': node.type === 'lineage',
                          'bg-amber-50 text-amber-700': node.type === 'excel_export',
                          'bg-purple-50 text-purple-700': node.type === 'http',
                        }">{{ node.type }}</span>
                </div>
                <div class="flex items-center gap-1">
                  <button class="rounded-md border border-slate-200 px-2 py-1 text-[10px] font-bold text-slate-600 transition hover:bg-slate-50 disabled:opacity-30" :disabled="index === 0" @click="moveWorkflowNode(index, -1)">↑</button>
                  <button class="rounded-md border border-slate-200 px-2 py-1 text-[10px] font-bold text-slate-600 transition hover:bg-slate-50 disabled:opacity-30" :disabled="index === workflowDraft.nodes.length - 1" @click="moveWorkflowNode(index, 1)">↓</button>
                  <button class="rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-[10px] font-bold text-rose-700 transition hover:bg-rose-100" @click="removeWorkflowNode(index)">删除</button>
                </div>
              </div>
              <div class="grid grid-cols-1 gap-2 lg:grid-cols-3">
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">类型</span>
                  <select v-model="node.type" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                    <option value="params">参数准备 params</option>
                    <option value="compare">数据对比 compare</option>
                    <option value="lineage">血缘分析 lineage</option>
                    <option value="excel_export">Excel 导出 excel_export</option>
                    <option value="http">HTTP 请求 http</option>
                  </select>
                </label>
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">显示名称</span>
                  <input v-model="node.name" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                </label>
              </div>
              <!-- compare 节点编辑器（含任务选择 + SQL drill-in 覆盖） -->
              <WorkflowCompareNodeEditor v-if="node.type === 'compare'" :node="node" class="mt-3" />

              <!-- params 节点编辑器（参数列表 + 引用语法速查） -->
              <WorkflowParamsNodeEditor v-if="node.type === 'params'" :node="node" class="mt-3" />

              <!-- lineage 节点编辑器（SQL + 方言） -->
              <WorkflowLineageNodeEditor v-if="node.type === 'lineage'" :node="node" class="mt-2" />
              <div v-if="node.type === 'http'" class="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-[100px_minmax(0,1fr)_120px]">
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">方法</span>
                  <select v-model="node.method" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                    <option>GET</option><option>POST</option><option>PUT</option>
                  </select>
                </label>
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">URL</span>
                  <input v-model="node.url" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs">
                </label>
                <label>
                  <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">期望状态</span>
                  <input v-model="node.expect_status" type="number" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs">
                </label>
              </div>

              <!-- excel_export 节点编辑器（sheet 列表 + 节点输出 / 历史 run 切换） -->
              <WorkflowExcelExportNodeEditor v-if="node.type === 'excel_export'" :node="node" class="mt-3" />
              <div v-if="otherNodeIds(node.id).length" class="mt-2">
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">依赖（depends_on）</span>
                <div class="flex flex-wrap gap-1.5">
                  <label v-for="otherId in otherNodeIds(node.id)" :key="otherId" class="flex cursor-pointer items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-0.5 text-xs hover:bg-blue-50">
                    <input type="checkbox" :value="otherId" v-model="node.depends_on" class="h-3 w-3 rounded">
                    <span class="font-mono text-slate-700">{{ otherId }}</span>
                  </label>
                </div>
              </div>
              <label class="mt-2 block">
                <span class="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-slate-400">条件 when（可选）</span>
                <input v-model="node.when" class="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs" placeholder="${nodes.x.summary.diff} > 0">
              </label>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>
