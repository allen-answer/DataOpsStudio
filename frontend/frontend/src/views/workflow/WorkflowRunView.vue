<script setup>
import { computed, inject, ref } from 'vue'
import { nodeStatusMeta, synthesizeEvents, parameterTypeMeta } from '../../mock/workflow_meta'

const emit = defineEmits(['back', 'open-detail'])
const { workflowResult, currentWorkflow, runWorkflow, runWorkflowAsync } = inject('app')

const run = computed(() => workflowResult.value)
const selectedNodeId = ref('')

// 真实参数类型来源：当前 workflow 里 type=params 节点的 config.parameters。
// 之前从 mock workflow_meta.js 取，会把示例参数（biz_date / batch_id ...）
// 也渲染成"真实运行变量"，造成混淆 —— 改成只用后端配置。
const realParamSpecs = computed(() => {
  const map = {}
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

// --- per-node 输出视图辅助 ---
const formatBytes = (n) => {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}
const formatNumber = (n) => (n == null ? '—' : n.toLocaleString('en-US'))

// compare 节点：四种归类的颜色
const compareBuckets = [
  { key: 'only_source', label: '仅源端', barClass: 'bg-rose-500',    textClass: 'text-rose-700' },
  { key: 'only_target', label: '仅目标', barClass: 'bg-amber-500',   textClass: 'text-amber-700' },
  { key: 'diff',        label: '字段差异', barClass: 'bg-orange-500',  textClass: 'text-orange-700' },
  { key: 'same',        label: '一致',   barClass: 'bg-emerald-500', textClass: 'text-emerald-700' },
]

// compare 节点 samples 第一段（diff 优先，其次 only_source / only_target），用于预览
const compareSamplePreview = (output) => {
  const samples = output?.samples || {}
  for (const key of ['diff', 'only_source', 'only_target']) {
    const arr = samples[key]
    if (Array.isArray(arr) && arr.length) {
      return { key, label: { diff: '差异', only_source: '仅源端', only_target: '仅目标' }[key], rows: arr.slice(0, 5), total: arr.length }
    }
  }
  return null
}

// 把 compare sample 行（{key, source, target, changes?}）拍平成可展示的 cell 数组
const flattenSampleRow = (row, kind) => {
  if (kind === 'only_source') return { keyText: JSON.stringify(row.key), payload: row.source }
  if (kind === 'only_target') return { keyText: JSON.stringify(row.key), payload: row.target }
  // diff: 显示变化字段
  const changes = row.changes || {}
  const changeText = Object.entries(changes).map(([col, v]) => `${col}: ${JSON.stringify(v.source)} → ${JSON.stringify(v.target)}`).join(' · ') || '(无字段差异)'
  return { keyText: JSON.stringify(row.key), payload: changeText }
}

// 折叠状态
const showRawJson = ref({})   // node_id → bool
const toggleRawJson = (id) => { showRawJson.value[id] = !showRawJson.value[id] }

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

            <!-- 节点输出（按 type 结构化） -->
            <div v-if="selectedNode.output && Object.keys(selectedNode.output).length" class="border-b border-slate-100 px-4 py-3">

              <!-- compare 节点：4 个统计卡 + 任务文件下载 + samples 预览 -->
              <div v-if="selectedNode.type === 'compare' && selectedNode.output.summary">
                <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">数据对比结果</p>
                <div class="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
                  <div v-for="b in compareBuckets" :key="b.key" class="rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
                    <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{{ b.label }}</p>
                    <p class="mt-0.5 font-mono text-xl font-bold tabular-nums" :class="b.textClass">{{ formatNumber(selectedNode.output.summary[b.key]) }}</p>
                  </div>
                </div>
                <div class="mt-2 flex flex-wrap items-center gap-2 text-[11.5px] text-slate-600">
                  <span>源端 <span class="font-mono font-semibold text-slate-700">{{ formatNumber(selectedNode.output.source_rows) }}</span> 行</span>
                  <span class="text-slate-300">·</span>
                  <span>目标 <span class="font-mono font-semibold text-slate-700">{{ formatNumber(selectedNode.output.target_rows) }}</span> 行</span>
                  <span v-if="selectedNode.output.task_name" class="text-slate-300">·</span>
                  <span v-if="selectedNode.output.task_name">任务 <span class="font-mono text-slate-700">{{ selectedNode.output.task_name }}</span></span>
                </div>
                <div v-if="selectedNode.output.excel_filename || selectedNode.output.result_filename" class="mt-2 flex flex-wrap gap-2">
                  <a v-if="selectedNode.output.excel_filename"
                     :href="`/results/${selectedNode.output.excel_filename}`"
                     target="_blank"
                     class="inline-flex h-7 items-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 text-[11px] font-semibold text-emerald-700 transition hover:bg-emerald-100">
                    ⬇ Excel 结果
                  </a>
                  <a v-if="selectedNode.output.result_filename"
                     :href="`/results/${selectedNode.output.result_filename}`"
                     target="_blank"
                     class="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">
                    ⬇ JSON
                  </a>
                </div>
                <!-- samples 预览（最多 5 行） -->
                <div v-if="compareSamplePreview(selectedNode.output)" class="mt-3 rounded-lg border border-slate-200">
                  <div class="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-1.5">
                    <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">{{ compareSamplePreview(selectedNode.output).label }} 预览（前 5 行 / 共 {{ compareSamplePreview(selectedNode.output).total }}）</span>
                  </div>
                  <table class="w-full text-[11.5px]">
                    <thead class="bg-slate-50/60">
                      <tr><th class="px-3 py-1.5 text-left font-semibold text-slate-500 w-[180px]">主键</th><th class="px-3 py-1.5 text-left font-semibold text-slate-500">{{ compareSamplePreview(selectedNode.output).key === 'diff' ? '字段差异' : '行内容' }}</th></tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100">
                      <tr v-for="(row, i) in compareSamplePreview(selectedNode.output).rows" :key="i">
                        <td class="px-3 py-1 font-mono text-[10.5px] text-slate-700 whitespace-nowrap">{{ flattenSampleRow(row, compareSamplePreview(selectedNode.output).key).keyText }}</td>
                        <td class="px-3 py-1 font-mono text-[10.5px] text-slate-700 break-all">{{ flattenSampleRow(row, compareSamplePreview(selectedNode.output).key).payload }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <!-- excel_export 节点：文件卡 + sheet 表 -->
              <div v-else-if="selectedNode.type === 'excel_export'">
                <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">Excel 导出</p>
                <div class="mt-2 rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
                  <div class="flex items-center justify-between gap-2">
                    <div class="min-w-0">
                      <p class="font-mono text-[12px] font-semibold text-slate-800 truncate">{{ selectedNode.output.filename || '—' }}</p>
                      <p class="mt-0.5 text-[10.5px] text-slate-500">
                        {{ formatBytes(selectedNode.output.file_size) }} ·
                        {{ selectedNode.output.sheet_count }} 个 sheet ·
                        共 <span class="font-mono">{{ formatNumber(selectedNode.output.total_rows_written) }}</span> 行
                      </p>
                    </div>
                    <a v-if="selectedNode.output.filename"
                       :href="`/results/${selectedNode.output.relative_path || selectedNode.output.filename}`"
                       target="_blank"
                       class="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg bg-emerald-600 px-3 text-[11px] font-semibold text-white transition hover:bg-emerald-700">
                      ⬇ 下载
                    </a>
                  </div>
                </div>
                <div v-if="(selectedNode.output.sheets || []).length" class="mt-2 rounded-lg border border-slate-200">
                  <table class="w-full text-[11.5px]">
                    <thead class="bg-slate-50/60">
                      <tr>
                        <th class="px-3 py-1.5 text-left font-semibold text-slate-500">Sheet</th>
                        <th class="px-3 py-1.5 text-left font-semibold text-slate-500">来源</th>
                        <th class="px-3 py-1.5 text-right font-semibold text-slate-500">行数</th>
                        <th class="px-3 py-1.5 text-left font-semibold text-slate-500">状态</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100">
                      <template v-for="(sh, i) in selectedNode.output.sheets" :key="i">
                        <tr>
                          <td class="px-3 py-1 font-semibold text-slate-700">{{ sh.name }}</td>
                          <td class="px-3 py-1 font-mono text-[10.5px] text-slate-600">
                            <span v-if="sh.source_type === 'history_run'" class="rounded bg-purple-50 px-1 py-0.5 text-purple-700 ring-1 ring-inset ring-purple-200" :title="`run ${sh.run_id}`">历史</span>
                            {{ (sh.node_id || sh.source_node) || '默认' }}<span class="text-slate-300">.</span>{{ (sh.dataset || sh.source_field) || '*' }}
                          </td>
                          <td class="px-3 py-1 text-right font-mono tabular-nums text-slate-700">{{ formatNumber(sh.rows_written) }} / {{ formatNumber(sh.max_rows) }}</td>
                          <td class="px-3 py-1">
                            <span v-if="sh.truncated" class="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-bold text-amber-700 ring-1 ring-inset ring-amber-200" title="超出 max_rows，已截断">⚠ 截断</span>
                            <span v-else-if="!sh.source_resolved" class="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-700 ring-1 ring-inset ring-rose-200">空 sheet</span>
                            <span v-else-if="sh.rows_written === 0" class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500" title="数据源解析成功但本身无数据">0 行</span>
                            <span v-else class="text-emerald-600">✓</span>
                          </td>
                        </tr>
                        <!-- 失败原因展开行：解析失败时让用户一眼看到为啥 -->
                        <tr v-if="!sh.source_resolved && sh.unresolved_reason">
                          <td colspan="4" class="border-t-0 px-3 pb-2 pt-0">
                            <div class="rounded border border-rose-200 bg-rose-50/60 px-2.5 py-1.5 text-[11px] text-rose-800">
                              <span class="font-semibold">原因：</span>
                              <span class="font-mono">{{ sh.unresolved_reason }}</span>
                            </div>
                          </td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </div>

              <!-- params 节点：解析后参数表 -->
              <div v-else-if="selectedNode.type === 'params'">
                <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">参数解析（{{ Object.keys(selectedNode.output).length }} 个）</p>
                <div class="mt-2 rounded-lg border border-slate-200">
                  <table class="w-full text-[11.5px]">
                    <thead class="bg-slate-50/60">
                      <tr><th class="px-3 py-1.5 text-left font-semibold text-slate-500 w-[160px]">参数</th><th class="px-3 py-1.5 text-left font-semibold text-slate-500">值</th></tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100">
                      <tr v-for="(value, name) in selectedNode.output" :key="name">
                        <td class="px-3 py-1 font-mono font-semibold text-slate-700">{{ name }}</td>
                        <td class="px-3 py-1 font-mono text-[10.5px] text-slate-600 break-all">
                          <span v-if="Array.isArray(value)">
                            <span v-for="(v, i) in value" :key="i" class="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{{ v }}</span>
                          </span>
                          <span v-else>{{ value }}</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <!-- http 节点：状态 + body 预览 -->
              <div v-else-if="selectedNode.type === 'http'">
                <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">HTTP 响应</p>
                <div class="mt-2 flex items-center gap-3 text-[12px]">
                  <span class="rounded px-2 py-0.5 font-mono font-bold ring-1 ring-inset"
                        :class="selectedNode.output.status >= 200 && selectedNode.output.status < 300 ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : 'bg-rose-50 text-rose-700 ring-rose-200'">
                    {{ selectedNode.output.status }}
                  </span>
                  <span v-if="selectedNode.output.truncated" class="text-[10.5px] text-amber-700">⚠ 响应体已截断（256 KB）</span>
                </div>
                <pre v-if="selectedNode.output.body" class="mt-2 max-h-48 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-[11px] leading-relaxed text-slate-700">{{ selectedNode.output.body }}</pre>
              </div>

              <!-- lineage 节点：source/target 计数 + warnings -->
              <div v-else-if="selectedNode.type === 'lineage'">
                <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">血缘分析</p>
                <div class="mt-2 grid grid-cols-3 gap-2">
                  <div class="rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
                    <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">来源表</p>
                    <p class="mt-0.5 font-mono text-lg font-bold tabular-nums text-slate-700">{{ formatNumber((selectedNode.output.sources || []).length) }}</p>
                  </div>
                  <div class="rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
                    <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">目标表</p>
                    <p class="mt-0.5 font-mono text-lg font-bold tabular-nums text-slate-700">{{ formatNumber((selectedNode.output.targets || []).length) }}</p>
                  </div>
                  <div class="rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
                    <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">血缘边</p>
                    <p class="mt-0.5 font-mono text-lg font-bold tabular-nums text-slate-700">{{ formatNumber((selectedNode.output.edges || []).length) }}</p>
                  </div>
                </div>
                <div v-if="(selectedNode.output.warnings || []).length" class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                  <p class="text-[10.5px] font-semibold uppercase tracking-wider text-amber-700">⚠ {{ selectedNode.output.warnings.length }} 个警告</p>
                  <ul class="mt-1 space-y-0.5 text-[11px] text-amber-900">
                    <li v-for="(w, i) in selectedNode.output.warnings.slice(0, 5)" :key="i" class="font-mono">{{ typeof w === 'string' ? w : JSON.stringify(w) }}</li>
                  </ul>
                </div>
              </div>

              <!-- 未知类型：fallback 到 JSON -->
              <div v-else>
                <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">节点输出 ({{ Object.keys(selectedNode.output).length }} keys)</p>
                <pre class="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ JSON.stringify(selectedNode.output, null, 2) }}</pre>
              </div>

              <!-- 折叠的原始 JSON（任何 type 都给一个逃生通道）-->
              <div class="mt-3">
                <button class="text-[10.5px] font-mono text-slate-500 transition hover:text-slate-700"
                        @click="toggleRawJson(selectedNode.node_id)">
                  {{ showRawJson[selectedNode.node_id] ? '▾' : '▸' }} 原始 output JSON
                </button>
                <pre v-if="showRawJson[selectedNode.node_id]" class="mt-1 max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ JSON.stringify(selectedNode.output, null, 2) }}</pre>
              </div>

              <p class="mt-2 font-mono text-[10.5px] text-slate-400">下游可通过 <code>{{ '${nodes.' + selectedNode.node_id + '.<path>}' }}</code> 引用</p>
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
