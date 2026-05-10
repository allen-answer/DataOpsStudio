<script setup lang="ts">
// Phase 11 MVP #3 —— trace-compare 跑完后的链式结果可视化。
//
// 检测 workflow_run.nodes 里 config._trace_compare meta，把每跳变成一个
// (upstream → downstream) 边，按 compare 节点的 summary（only_source / diff /
// only_target / same）给每条边判定结果：
//   - 绿（一致）：only_source=0 + only_target=0 + diff=0
//   - 红（偏离）：上述任一 > 0
//   - 灰（未跑/失败/跳过）：节点未 success 或 output 缺 summary
//
// 用户拿到这个面板的核心价值：一眼看到链上**首次发生数据偏离的位置**，
// 直接锁定哪一层是污染源。
import { computed } from 'vue'
import { ArrowRight, AlertTriangle, CheckCircle2, MinusCircle } from 'lucide-vue-next'

interface CompareSummary {
  only_source: number
  only_target: number
  diff: number
  same: number
}
interface NodeOutput {
  summary?: CompareSummary
  source_rows?: number
  target_rows?: number
  [key: string]: unknown
}
interface TraceMeta {
  hop: number
  strategy: string
  upstream: { table: string; column: string }
  downstream: { table: string; column: string }
  datasource_source?: string
  datasource_target?: string
  unmapped_tables?: string[]
}
interface NodeRun {
  node_id: string
  name?: string
  status: string
  config?: { _trace_compare?: TraceMeta; [k: string]: unknown }
  output?: NodeOutput
  error?: string
}
interface WorkflowRunLike {
  run_id?: string
  status?: string
  nodes?: NodeRun[]
}

const props = defineProps<{ run: WorkflowRunLike | null | undefined }>()
const emit = defineEmits<{ (e: 'select-node', nodeId: string): void }>()

interface TraceEdge {
  nodeId: string
  nodeName: string
  hop: number
  upstream: { table: string; column: string }
  downstream: { table: string; column: string }
  status: string
  verdict: 'same' | 'diff' | 'unrun'
  summary: CompareSummary | null
  totalDelta: number
  sourceRows: number
  targetRows: number
  error: string
}

const edges = computed<TraceEdge[]>(() => {
  const out: TraceEdge[] = []
  for (const node of (props.run?.nodes || [])) {
    const meta = node.config?._trace_compare
    if (!meta) continue
    const summary: CompareSummary | null = (node.output?.summary as CompareSummary) || null
    let verdict: 'same' | 'diff' | 'unrun' = 'unrun'
    if (node.status === 'success' && summary) {
      const delta = (summary.only_source || 0) + (summary.only_target || 0) + (summary.diff || 0)
      verdict = delta === 0 ? 'same' : 'diff'
    }
    const totalDelta = summary
      ? (summary.only_source || 0) + (summary.only_target || 0) + (summary.diff || 0)
      : 0
    out.push({
      nodeId: node.node_id,
      nodeName: node.name || node.node_id,
      hop: meta.hop,
      upstream: meta.upstream,
      downstream: meta.downstream,
      status: node.status,
      verdict,
      summary,
      totalDelta,
      sourceRows: Number(node.output?.source_rows) || 0,
      targetRows: Number(node.output?.target_rows) || 0,
      error: node.error || '',
    })
  }
  // hop 升序：第 1 跳 = focal 的直接上游（链尾），最大 hop = 链根（最远的 upstream）
  out.sort((a, b) => a.hop - b.hop)
  return out
})

const isTraceRun = computed<boolean>(() => edges.value.length > 0)

// 「首次偏离 hop」—— 用户最关心的诊断信号
const firstDivergentHop = computed<TraceEdge | null>(() => {
  // 业务语义：从最远 upstream（最大 hop）走向 focal 时，第一个 verdict=diff 的边
  // 就是污染源。所以倒序扫。
  const sorted = [...edges.value].sort((a, b) => b.hop - a.hop)
  return sorted.find((e) => e.verdict === 'diff') || null
})

// 链路总体：全 same → 数据一致；有 diff → 部分偏离；全未跑 → 未运行
const overallStatus = computed<'consistent' | 'divergent' | 'partial' | 'pending'>(() => {
  if (!edges.value.length) return 'pending'
  const same = edges.value.filter((e) => e.verdict === 'same').length
  const diff = edges.value.filter((e) => e.verdict === 'diff').length
  const unrun = edges.value.filter((e) => e.verdict === 'unrun').length
  if (diff === 0 && unrun === 0) return 'consistent'
  if (diff > 0 && unrun === 0) return 'divergent'
  if (diff === 0 && unrun === edges.value.length) return 'pending'
  return 'partial'
})

function edgeColorClass(edge: TraceEdge): string {
  if (edge.verdict === 'same') return 'bg-emerald-100 text-emerald-800 ring-emerald-300'
  if (edge.verdict === 'diff') return 'bg-rose-100 text-rose-800 ring-rose-300'
  return 'bg-slate-100 text-slate-600 ring-slate-300'
}

function edgeArrowColor(edge: TraceEdge): string {
  if (edge.verdict === 'same') return 'text-emerald-500'
  if (edge.verdict === 'diff') return 'text-rose-500'
  return 'text-slate-300'
}
</script>

<template>
  <div v-if="isTraceRun" class="rounded-xl border border-purple-200 bg-purple-50/40 p-3 shadow-sm">
    <div class="mb-2 flex items-center justify-between">
      <div>
        <p class="text-[11px] font-bold uppercase tracking-wider text-purple-700">
          ✨ 字段血缘溯源结果
        </p>
        <p class="muted mt-0.5 text-[10.5px]">
          此 run 是 trace-compare 链式对比 ·
          共 {{ edges.length }} 跳
          <span v-if="overallStatus === 'consistent'" class="ml-1 text-emerald-700">
            · 全链数据一致 ✓
          </span>
          <span v-else-if="overallStatus === 'divergent'" class="ml-1 text-rose-700">
            · 全链有偏离 ⚠
          </span>
          <span v-else-if="overallStatus === 'partial'" class="ml-1 text-amber-700">
            · 部分跳未跑/失败
          </span>
          <span v-else class="ml-1 text-slate-500">
            · 未运行
          </span>
        </p>
      </div>
      <div v-if="firstDivergentHop" class="rounded bg-rose-100 px-2 py-1 text-[10.5px] font-semibold text-rose-700 ring-1 ring-rose-200">
        <AlertTriangle class="mr-0.5 inline h-3 w-3" />
        首次偏离 hop {{ firstDivergentHop.hop }}：{{ firstDivergentHop.upstream.table }} → {{ firstDivergentHop.downstream.table }}
      </div>
    </div>

    <!-- 链路图：水平排列每条边，按 verdict 着色 -->
    <div class="overflow-x-auto">
      <div class="flex items-stretch gap-1 pb-1">
        <template v-for="(edge, idx) in edges" :key="edge.nodeId">
          <button
            class="flex min-w-[170px] cursor-pointer flex-col items-stretch rounded-lg border ring-1 ring-inset px-2 py-1.5 text-left transition hover:scale-[1.02]"
            :class="edgeColorClass(edge)"
            :title="`点击跳到节点 ${edge.nodeName}`"
            @click="emit('select-node', edge.nodeId)"
          >
            <span class="flex items-center justify-between text-[9.5px] font-bold uppercase tracking-wider opacity-80">
              <span>hop {{ edge.hop }}</span>
              <CheckCircle2 v-if="edge.verdict === 'same'" class="h-3 w-3" />
              <AlertTriangle v-else-if="edge.verdict === 'diff'" class="h-3 w-3" />
              <MinusCircle v-else class="h-3 w-3 opacity-60" />
            </span>
            <span class="sql-font mt-0.5 text-[10.5px] font-semibold">
              {{ edge.upstream.table }}.{{ edge.upstream.column }}
            </span>
            <span class="sql-font text-[9.5px] opacity-70">
              ↓
            </span>
            <span class="sql-font text-[10.5px] font-semibold">
              {{ edge.downstream.table }}.{{ edge.downstream.column }}
            </span>
            <span v-if="edge.summary" class="mt-1 grid grid-cols-4 gap-0.5 text-[9px] opacity-90">
              <span :title="`${edge.summary.same} 行一致`">✓ {{ edge.summary.same }}</span>
              <span :title="`${edge.summary.diff} 行 PK 同 / 值不同`">≠ {{ edge.summary.diff }}</span>
              <span :title="`${edge.summary.only_source} 行仅 source 有`">L {{ edge.summary.only_source }}</span>
              <span :title="`${edge.summary.only_target} 行仅 target 有`">R {{ edge.summary.only_target }}</span>
            </span>
            <span v-else-if="edge.error" class="muted mt-1 line-clamp-2 text-[9.5px]">
              ⚠ {{ edge.error }}
            </span>
            <span v-else class="muted mt-1 text-[9.5px]">
              {{ edge.status === 'pending' ? '排队' : edge.status }}
            </span>
          </button>
          <ArrowRight v-if="idx < edges.length - 1"
            class="my-auto h-4 w-4 shrink-0"
            :class="edgeArrowColor(edge)"
          />
        </template>
      </div>
    </div>

    <p class="muted mt-2 text-[10px]">
      点击任意 hop 在下方节点详情面板里看完整 compare 结果（差异行样本 / Excel 下载）。
      <span class="opacity-60">绿 = 全 same，红 = 有偏离，灰 = 未运行 / 失败 / 跳过。</span>
    </p>
  </div>
</template>
