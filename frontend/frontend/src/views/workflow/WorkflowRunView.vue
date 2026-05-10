<script setup lang="ts">
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { nodeStatusMeta, synthesizeEvents, parameterTypeMeta } from '../../mock/workflow_meta'
import WorkflowRunNodeDetail from '../../components/workflow/WorkflowRunNodeDetail.vue'
import { useWorkflowStore } from '../../stores/workflow'

const emit = defineEmits(['back', 'open-detail'])
const workflowStore = useWorkflowStore()
const {
  workflowResult, currentWorkflow,
  workflowAsyncJob, workflowAsyncStatus,
} = storeToRefs(workflowStore)
const {
  runWorkflow, runWorkflowAsync, runWorkflowAsyncWith,
  rerunWorkflowFromNode, cancelWorkflowAsync, reemitWorkflowRunOpenLineage,
} = workflowStore

// 历史 run 复用变量重跑：剥掉内置变量，避免冻结时间。和 detail view 共享
// 同样的 helper 语义。
const REUSABLE_BUILTIN_KEYS = new Set(['today', 'now', 'year', 'month', 'day'])
const reusableVars = (vars: Record<string, unknown> | null | undefined): Record<string, unknown> => {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(vars || {})) {
    if (!REUSABLE_BUILTIN_KEYS.has(k)) out[k] = v
  }
  return out
}

const rerunSameVars = (): void => {
  if (!run.value) return
  runWorkflowAsyncWith(run.value.workflow_id, reusableVars(run.value.variables))
}
const rerunDefaults = (): void => {
  if (!run.value) return
  runWorkflowAsyncWith(run.value.workflow_id, {})
}

// 局部重跑：从指定节点起跑。上游沿用本次 run 的 output（reused），自身和
// 下游全部重跑。变量沿用本次（不传 variables → 后端复用上次 run.variables）。
const rerunFromNode = (nodeId: string): void => {
  if (!run.value || !nodeId) return
  rerunWorkflowFromNode(run.value.run_id, nodeId)
}

// "终止"按钮只在本 run 是活跃后台任务且未结束时可用 —— 否则当前界面看到的
// 是历史 run 的快照，"终止"无意义。
const canCancel = computed(() => {
  if (!run.value || !workflowAsyncJob.value) return false
  // job 是后端 task or workflow run；用 run_id 比对（async submit 时 run_id 才挂上）
  if (workflowAsyncStatus.value?.status === 'running') {
    // 当前展示的 run 必须就是后台任务对应的那次（result 里的 run_id 一致）
    const activeRunId = workflowAsyncStatus.value?.result?.run_id
    if (activeRunId && activeRunId === run.value.run_id) return true
    // 或后台任务尚未产出 run（仍在排队），workflow_id 一致也算
    if (workflowAsyncStatus.value.workflow_id === run.value.workflow_id) return true
  }
  return false
})

const run = computed(() => workflowResult.value)
const selectedNodeId = ref<string>('')

// 真实参数类型来源：当前 workflow 里 type=params 节点的 config.parameters。
// 之前从 mock workflow_meta.js 取，会把示例参数（biz_date / batch_id ...）
// 也渲染成"真实运行变量"，造成混淆 —— 改成只用后端配置。
const realParamSpecs = computed<Record<string, any>>(() => {
  const map: Record<string, any> = {}
  for (const node of currentWorkflow.value?.nodes || []) {
    if (node.type !== 'params') continue
    for (const p of node.config?.parameters || []) {
      if (p?.name) map[p.name] = p
    }
  }
  return map
})

// chip 只展示本次 run 真实写入的 variables；类型从 realParamSpecs 取，
// 没有 spec 的内置变量（today / now / year / month / day）退化为 fixed。
const runParameterChips = computed(() => {
  const vars = run.value?.variables || {}
  return Object.keys(vars).map((name) => ({
    name,
    value: vars[name],
    type: realParamSpecs.value[name]?.type || 'fixed',
  }))
})

// 自动选第一个失败节点（如果有的话），否则第一个节点
const initialNodeId = computed(() => {
  if (!run.value) return ''
  const failed = run.value.nodes?.find((n) => n.status === 'failed')
  if (failed) return failed.node_id
  return run.value.nodes?.[0]?.node_id || ''
})
if (!selectedNodeId.value && initialNodeId.value) selectedNodeId.value = initialNodeId.value

const selectedNode = computed(() => run.value?.nodes?.find((n) => n.node_id === selectedNodeId.value))

// Gantt 计算：相对开始时间转秒数 offset。
const ganttData = computed(() => {
  if (!run.value || !run.value.nodes) return { steps: [], totalSeconds: 1 }
  const startTs = parseTs(run.value.started_at)
  const steps = []
  let total = run.value.elapsed_seconds || 1
  for (const n of run.value.nodes) {
    const offsetSec = n.started_at && startTs ? (parseTs(n.started_at) - startTs) / 1000 : 0
    const duration = n.elapsed_seconds || 0
    if (offsetSec + duration > total) total = offsetSec + duration
    steps.push({
      node: n,
      offsetSec: Math.max(0, offsetSec),
      duration,
    })
  }
  return { steps, totalSeconds: Math.max(total, 1) }
})

function parseTs(s: string | undefined | null): number | null {
  if (!s) return null
  // "2026-05-02T10:00:00" or "2026-05-02 10:00:00"
  const normalized = s.includes('T') ? s : s.replace(' ', 'T')
  const t = Date.parse(normalized)
  return isFinite(t) ? t : null
}

const stepBarStyle = (step: { offsetSec: number; duration: number }) => ({
  left: `${(step.offsetSec / ganttData.value.totalSeconds) * 100}%`,
  width: `${Math.max(0.5, (step.duration / ganttData.value.totalSeconds) * 100)}%`,
})

// 事件流合成
const events = computed(() => synthesizeEvents(run.value))
const openLineageResults = computed(() => run.value?.integrations?.openlineage || [])
const openLineageOkCount = computed(() => openLineageResults.value.filter((item) => item.ok).length)

// 选中节点的事件 —— 传给 WorkflowRunNodeDetail 渲染
const selectedNodeEvents = computed(() => events.value.filter((ev) => ev.step === selectedNodeId.value))

// Run 顶部状态 pill 细分。后端只发 success / failed / cancelled / running，
// 这里把 success + 有 skipped 节点拆成"部分跳过"，区分"全成功"和"有 when
// 跳过"——便于一眼知道是不是有节点被条件路由跳过。
const runStatusDisplay = computed(() => {
  if (!run.value) return null
  const status = run.value.status
  const skipped = (run.value.nodes || []).filter((n) => n.status === 'skipped').length
  if (status === 'success' && skipped > 0) {
    return {
      label: `部分跳过 (${skipped})`,
      pillClass: 'bg-amber-50 text-amber-700 ring-amber-200',
      dotClass:  'bg-amber-500',
      hint: `${skipped} 个节点未执行（when 条件 false 或上游跳过级联）`,
    }
  }
  if (status === 'success') {
    return { label: '成功', pillClass: 'bg-emerald-50 text-emerald-700 ring-emerald-200', dotClass: 'bg-emerald-500', hint: '' }
  }
  if (status === 'failed') {
    return { label: '失败', pillClass: 'bg-rose-50 text-rose-700 ring-rose-200', dotClass: 'bg-rose-500', hint: '' }
  }
  if (status === 'cancelled') {
    return { label: '已取消', pillClass: 'bg-slate-100 text-slate-600 ring-slate-200', dotClass: 'bg-slate-400', hint: '' }
  }
  if (status === 'running') {
    return { label: '运行中', pillClass: 'bg-blue-50 text-blue-700 ring-blue-200', dotClass: 'bg-blue-500 animate-pulse', hint: '' }
  }
  return { label: status, pillClass: 'bg-slate-100 text-slate-600 ring-slate-200', dotClass: 'bg-slate-400', hint: '' }
})
</script>

<template>
  <div v-if="!run" class="rounded-xl border border-dashed border-slate-200 bg-white p-12 text-center text-sm text-slate-400">
    请先在「作业流详情」的「运行历史」中选择一次运行
  </div>

  <div v-else class="flex flex-col gap-3">
    <!-- header -->
    <div class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-[11px] text-slate-500">
            <button class="text-slate-600 transition hover:text-blue-600" @click="emit('back')">← 作业流详情</button>
            <span class="text-slate-300">/</span>
            <button class="text-slate-600 transition hover:text-blue-600" @click="emit('open-detail', run.workflow_id)">{{ run.workflow_name }}</button>
            <span class="text-slate-300">/</span>
            <span class="font-mono">{{ run.run_id }}</span>
          </div>
          <div class="mt-1 flex items-center gap-2">
            <span class="rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset"
                  :class="runStatusDisplay?.pillClass"
                  :title="runStatusDisplay?.hint || ''">
              <span class="mr-1 inline-block h-1.5 w-1.5 rounded-full" :class="runStatusDisplay?.dotClass"></span>
              {{ runStatusDisplay?.label }}
            </span>
            <span class="font-mono text-[12.5px] text-slate-700">耗时 {{ run.elapsed_seconds }}s</span>
            <span class="text-slate-300">·</span>
            <span class="font-mono text-[12px] text-slate-500">{{ run.started_at }} → {{ run.finished_at }}</span>
            <span class="text-slate-300">·</span>
            <span class="text-[11.5px] text-slate-500">触发：手动</span>
          </div>
          <div v-if="runParameterChips.length" class="mt-2 flex flex-wrap gap-1">
            <span v-for="chip in runParameterChips" :key="chip.name"
                  class="inline-flex items-center gap-1 rounded ring-1 ring-inset px-1.5 py-0.5 font-mono text-[10.5px]"
                  :class="parameterTypeMeta[chip.type]?.accent || parameterTypeMeta.fixed.accent">
              <span class="text-[9px] font-bold opacity-80">{{ parameterTypeMeta[chip.type]?.glyph || '◇' }}</span>
              <span class="font-semibold">{{ chip.name }}</span>
              <span class="opacity-50">=</span>
              <span>{{ chip.value }}</span>
            </span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button v-if="Object.keys(reusableVars(run.variables || {})).length"
                  class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-amber-600 px-3 text-xs font-semibold text-white transition hover:bg-amber-700"
                  :title="`复用本次的真实变量（${Object.keys(reusableVars(run.variables)).join(', ')}）重跑；today/now 等内置不复用`"
                  @click="rerunSameVars">
            ⟳ 复用变量重跑
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
                  title="按当前作业流配置 + 默认变量重跑"
                  @click="rerunDefaults">
            ↻ 重跑
          </button>
          <button v-if="canCancel"
                  class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-2.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100"
                  @click="cancelWorkflowAsync">
            ▣ 终止
          </button>
        </div>
      </div>

      <div v-if="openLineageResults.length" class="mt-3 rounded-lg border border-violet-100 bg-violet-50/50 px-3 py-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-[10.5px] font-semibold uppercase tracking-wider text-violet-700">
            OpenLineage · {{ openLineageOkCount }}/{{ openLineageResults.length }} sent
          </p>
          <button class="inline-flex h-7 items-center gap-1 rounded-lg border border-violet-200 bg-white px-2.5 text-[11px] font-semibold text-violet-700 transition hover:bg-violet-50"
                  @click="reemitWorkflowRunOpenLineage?.(run.run_id)">
            重新发送
          </button>
        </div>
        <div class="mt-2 grid gap-1 md:grid-cols-2">
          <div v-for="(item, i) in openLineageResults" :key="i" class="rounded border border-violet-100 bg-white px-2 py-1 text-[11px]">
            <span class="font-mono font-semibold" :class="item.ok ? 'text-emerald-700' : 'text-rose-700'">{{ item.event_type }}</span>
            <span class="text-slate-400"> · </span>
            <span class="break-all font-mono text-slate-600">{{ item.target || '(missing url)' }}</span>
            <p v-if="item.error" class="mt-0.5 break-all font-mono text-rose-700">{{ item.error }}</p>
          </div>
        </div>
      </div>

      <div v-else class="mt-3 flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
        <p class="text-[11px] text-slate-500">OpenLineage 未发送或未配置 webhook。</p>
        <button class="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
                @click="reemitWorkflowRunOpenLineage?.(run.run_id)">
          尝试发送
        </button>
      </div>

      <!-- summary stats -->
      <div class="mt-3 grid grid-cols-2 gap-2 md:grid-cols-5">
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">总节点</p>
          <p class="mt-0.5 font-mono text-base font-semibold text-slate-700">{{ run.nodes?.length || 0 }}</p>
        </div>
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">成功</p>
          <p class="mt-0.5 font-mono text-base font-semibold text-emerald-600">{{ run.nodes?.filter((n) => n.status === 'success').length || 0 }}</p>
        </div>
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">失败</p>
          <p class="mt-0.5 font-mono text-base font-semibold text-rose-600">{{ run.nodes?.filter((n) => n.status === 'failed').length || 0 }}</p>
        </div>
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">跳过</p>
          <p class="mt-0.5 font-mono text-base font-semibold text-slate-600">{{ run.nodes?.filter((n) => n.status === 'skipped').length || 0 }}</p>
        </div>
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">事件总数</p>
          <p class="mt-0.5 font-mono text-base font-semibold text-slate-700">{{ events.length }}</p>
        </div>
      </div>
    </div>

    <!-- gantt 时间线 -->
    <div class="rounded-xl border border-slate-200 bg-white shadow-sm">
      <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">步骤时间线 · 共 {{ Math.round(ganttData.totalSeconds) }} 秒</span>
        <span class="text-[10.5px] text-slate-400">点击步骤可在下方查看节点详情</span>
      </div>
      <div class="space-y-1 p-3">
        <button v-for="step in ganttData.steps" :key="step.node.node_id"
                class="grid w-full grid-cols-[180px_minmax(0,1fr)_70px] items-center gap-3 rounded-lg px-2 py-1.5 text-left transition"
                :class="selectedNodeId === step.node.node_id ? 'bg-blue-50 ring-1 ring-blue-200' : 'hover:bg-slate-50'"
                @click="selectedNodeId = step.node.node_id">
          <span class="flex items-center gap-1.5">
            <span class="h-1.5 w-1.5 rounded-full" :class="nodeStatusMeta[step.node.status]?.dot || 'bg-slate-300'"></span>
            <span class="truncate text-[12px] font-medium text-slate-700">{{ step.node.name || step.node.node_id }}</span>
          </span>
          <span class="relative h-3 rounded-full bg-slate-100">
            <span class="absolute top-0 h-3 rounded-full" :class="nodeStatusMeta[step.node.status]?.bar || 'bg-slate-300'" :style="stepBarStyle(step)"></span>
          </span>
          <span class="text-right font-mono text-[10.5px] text-slate-500">{{ step.duration }}秒</span>
        </button>
      </div>
    </div>

    <!-- 主区域：节点列表 + 节点详情 -->
    <div class="grid grid-cols-[280px_minmax(0,1fr)] gap-3">
      <!-- 节点列表 -->
      <aside class="flex flex-col rounded-xl border border-slate-200 bg-white shadow-sm">
        <div class="border-b border-slate-200 px-3 py-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">节点（{{ run.nodes?.length || 0 }}）</div>
        <div class="flex-1 overflow-auto">
          <button v-for="n in run.nodes || []" :key="n.node_id"
                  class="flex w-full items-center gap-2 border-b border-slate-100 px-3 py-2.5 text-left transition last:border-0"
                  :class="selectedNodeId === n.node_id ? 'bg-blue-50' : 'hover:bg-slate-50'"
                  @click="selectedNodeId = n.node_id">
            <span class="h-2 w-2 shrink-0 rounded-full" :class="nodeStatusMeta[n.status]?.dot || 'bg-slate-300'"></span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-[12.5px] font-semibold text-slate-700">{{ n.name || n.node_id }}</p>
              <p class="font-mono text-[10.5px] text-slate-500">{{ n.type }} · {{ n.elapsed_seconds }}s</p>
            </div>
            <span v-if="n.reused"
                  class="rounded bg-amber-50 px-1 py-0.5 text-[9.5px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-200"
                  title="本次没有实际执行此节点，沿用上一次 run 的输出">复用</span>
            <span class="rounded-full px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="nodeStatusMeta[n.status]?.pill || ''">{{ nodeStatusMeta[n.status]?.label }}</span>
          </button>
        </div>
      </aside>

      <!-- 节点详情：抽到 WorkflowRunNodeDetail。空态留在父容器（visual shell）。 -->
      <div v-if="!selectedNode" class="flex flex-col rounded-xl border border-slate-200 bg-white shadow-sm">
        <div class="grid h-full place-items-center text-sm text-slate-400">
          选择左侧的节点查看详情
        </div>
      </div>
      <WorkflowRunNodeDetail
        v-else
        :node="selectedNode"
        :node-events="selectedNodeEvents"
        :run-id="run.run_id"
        :workflow-id="run.workflow_id"
        @rerun-from-node="rerunFromNode"
        @rerun-defaults="rerunDefaults" />
    </div>
  </div>
</template>
