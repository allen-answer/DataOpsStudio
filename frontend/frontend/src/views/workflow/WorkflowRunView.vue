<script setup>
import { computed, inject, ref } from 'vue'
import { nodeStatusMeta, synthesizeEvents, getMeta, parameterTypeMeta } from '../../mock/workflow_meta'

const emit = defineEmits(['back', 'open-detail'])
const { state, workflowResult, currentWorkflow, runWorkflow, runWorkflowAsync } = inject('app')

const run = computed(() => workflowResult.value)
const selectedNodeId = ref('')

// 把运行的 variables 字典 + workflow 元数据中的参数定义合并起来。
// 这样运行参数 chip 既能显示原始 value，又能带上类型信息。
const wfIndex = computed(() => state.workflows.findIndex((wf) => wf.id === run.value?.workflow_id))
const wfMeta = computed(() => getMeta(currentWorkflow.value, wfIndex.value === -1 ? 0 : wfIndex.value))
const paramSpecs = computed(() => {
  const map = {}
  for (const p of wfMeta.value.parameters || []) map[p.name] = p
  return map
})
const runParameterChips = computed(() => {
  const chips = []
  const vars = run.value?.variables || {}
  // 优先按参数定义顺序展示，最后追加变量里有但定义里没有的
  for (const p of wfMeta.value.parameters || []) {
    chips.push({ name: p.name, value: vars[p.name] ?? '—', type: p.type })
  }
  for (const k of Object.keys(vars)) {
    if (!paramSpecs.value[k]) chips.push({ name: k, value: vars[k], type: 'fixed' })
  }
  return chips
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

function parseTs(s) {
  if (!s) return null
  // "2026-05-02T10:00:00" or "2026-05-02 10:00:00"
  const normalized = s.includes('T') ? s : s.replace(' ', 'T')
  const t = Date.parse(normalized)
  return isFinite(t) ? t : null
}

const stepBarStyle = (step) => ({
  left: `${(step.offsetSec / ganttData.value.totalSeconds) * 100}%`,
  width: `${Math.max(0.5, (step.duration / ganttData.value.totalSeconds) * 100)}%`,
})

// 事件流合成
const events = computed(() => synthesizeEvents(run.value))
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

// 选中节点的事件
const selectedNodeEvents = computed(() => events.value.filter((ev) => ev.step === selectedNodeId.value))
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
                  :class="run.status === 'success' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : 'bg-rose-50 text-rose-700 ring-rose-200'">
              <span class="mr-1 inline-block h-1.5 w-1.5 rounded-full" :class="run.status === 'success' ? 'bg-emerald-500' : 'bg-rose-500'"></span>
              {{ run.status === 'success' ? '成功' : run.status === 'failed' ? '失败' : run.status }}
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
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-amber-600 px-3 text-xs font-semibold text-white transition hover:bg-amber-700" @click="runWorkflow">
            ⟳ 重跑失败节点
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50" @click="runWorkflowAsync">
            重跑全部
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-2.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100">
            ▣ 终止
          </button>
        </div>
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
            <span class="rounded-full px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="nodeStatusMeta[n.status]?.pill || ''">{{ nodeStatusMeta[n.status]?.label }}</span>
          </button>
        </div>
      </aside>

      <!-- 节点详情 -->
      <div class="flex flex-col rounded-xl border border-slate-200 bg-white shadow-sm">
        <div v-if="!selectedNode" class="grid h-full place-items-center text-sm text-slate-400">
          选择左侧的节点查看详情
        </div>
        <template v-else>
          <div class="border-b border-slate-200 bg-slate-50/60 px-4 py-3">
            <div class="flex items-center gap-2">
              <span class="h-2.5 w-2.5 rounded-full" :class="nodeStatusMeta[selectedNode.status]?.dot"></span>
              <h3 class="text-[14px] font-bold text-slate-800">{{ selectedNode.name || selectedNode.node_id }}</h3>
              <span class="rounded px-1.5 py-0.5 text-[10px] font-mono font-bold uppercase"
                    :class="{ 'bg-blue-50 text-blue-700': selectedNode.type === 'compare', 'bg-emerald-50 text-emerald-700': selectedNode.type === 'lineage', 'bg-purple-50 text-purple-700': selectedNode.type === 'http' }">{{ selectedNode.type }}</span>
              <span class="rounded-full px-2 py-0.5 text-[10.5px] font-semibold ring-1 ring-inset" :class="nodeStatusMeta[selectedNode.status]?.pill">{{ nodeStatusMeta[selectedNode.status]?.label }}</span>
            </div>
            <dl class="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11.5px] md:grid-cols-4">
              <div><dt class="text-slate-400">开始</dt><dd class="font-mono text-slate-700">{{ selectedNode.started_at || '—' }}</dd></div>
              <div><dt class="text-slate-400">结束</dt><dd class="font-mono text-slate-700">{{ selectedNode.finished_at || '—' }}</dd></div>
              <div><dt class="text-slate-400">耗时</dt><dd class="font-mono text-slate-700">{{ selectedNode.elapsed_seconds }}s</dd></div>
              <div><dt class="text-slate-400">node_id</dt><dd class="font-mono text-slate-700">{{ selectedNode.node_id }}</dd></div>
            </dl>
          </div>

          <div class="flex-1 overflow-auto">
            <!-- 错误信息（如有） -->
            <div v-if="selectedNode.error" class="border-b border-rose-200 bg-rose-50 px-4 py-3">
              <p class="text-[11px] font-bold uppercase tracking-wider text-rose-700">错误</p>
              <pre class="mt-1 whitespace-pre-wrap font-mono text-[12px] text-rose-900">{{ selectedNode.error }}</pre>
              <div class="mt-2 flex gap-2">
                <button class="inline-flex h-7 items-center gap-1 rounded-lg bg-rose-600 px-2.5 text-[11px] font-semibold text-white transition hover:bg-rose-700">⟳ 重试此节点</button>
                <button class="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">复制堆栈</button>
              </div>
            </div>

            <!-- 节点输出 -->
            <div v-if="selectedNode.output && Object.keys(selectedNode.output).length" class="border-b border-slate-100 px-4 py-3">
              <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">节点输出 ({{ Object.keys(selectedNode.output).length }} keys)</p>
              <pre class="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ JSON.stringify(selectedNode.output, null, 2) }}</pre>
              <p class="mt-1.5 font-mono text-[10.5px] text-slate-400">下游可通过 ${'$'}{nodes.{{ selectedNode.node_id }}.&lt;path&gt;} 引用</p>
            </div>

            <!-- 该节点的事件 -->
            <div class="px-4 py-3">
              <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">本节点事件 ({{ selectedNodeEvents.length }})</p>
              <div v-if="!selectedNodeEvents.length" class="mt-2 rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-[11.5px] text-slate-400">无事件</div>
              <div v-else class="mt-2 font-mono text-[12px]">
                <div v-for="(ev, idx) in selectedNodeEvents" :key="idx" class="grid grid-cols-[120px_1fr] items-start gap-3 border-b border-slate-100 py-1.5 last:border-0">
                  <span class="text-[10.5px] text-slate-400">{{ ev.ts }}</span>
                  <div class="min-w-0">
                    <span class="text-[10.5px] font-bold uppercase tracking-wider" :class="eventTypeMeta[ev.type]?.text || 'text-slate-500'">
                      {{ eventTypeMeta[ev.type]?.glyph }} {{ eventTypeMeta[ev.type]?.label || ev.type }}
                    </span>
                    <p class="mt-0.5 break-words" :class="levelClass(ev.level)">{{ ev.msg }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
