<script setup>
import { computed, ref } from 'vue'
import { dagDetail, statusMeta, scheduleStatusMeta, nodeTypeMeta, nodePropertyTemplates } from '../../mock/operations'

const emit = defineEmits(['back', 'open-runs'])

const NODE_W = 192
const NODE_H = 60

const selectedNodeId = ref('transform')   // start with the failed node selected
const propertyTab = ref('base')

const selectedNode = computed(() => dagDetail.nodes.find((n) => n.id === selectedNodeId.value))
const selectedProps = computed(() => nodePropertyTemplates[selectedNodeId.value] || nodePropertyTemplates['extract'])

const canvas = computed(() => {
  let maxX = 0, maxY = 0
  for (const node of dagDetail.nodes) {
    if (node.x + NODE_W > maxX) maxX = node.x + NODE_W
    if (node.y + NODE_H > maxY) maxY = node.y + NODE_H
  }
  return { width: maxX + 80, height: maxY + 80 }
})

const nodeById = computed(() => {
  const map = {}
  for (const node of dagDetail.nodes) map[node.id] = node
  return map
})

// SVG path for an edge: right-edge of source → left-edge of target,
// with horizontal bezier handles for a clean orthogonal-ish curve.
const edgePath = (edge) => {
  const s = nodeById.value[edge.source]
  const t = nodeById.value[edge.target]
  if (!s || !t) return ''
  const sx = s.x + NODE_W
  const sy = s.y + NODE_H / 2
  const tx = t.x
  const ty = t.y + NODE_H / 2
  const dx = Math.max(40, (tx - sx) * 0.5)
  return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`
}

const edgeHighlighted = (edge) => {
  if (!selectedNodeId.value) return false
  return edge.source === selectedNodeId.value || edge.target === selectedNodeId.value
}

const summary = computed(() => {
  const counts = { success: 0, failed: 0, running: 0, pending: 0, waiting: 0 }
  for (const node of dagDetail.nodes) counts[node.status] = (counts[node.status] || 0) + 1
  return counts
})

const propertyTabs = [
  { id: 'base',    label: '基础信息' },
  { id: 'params',  label: '运行参数' },
  { id: 'retry',   label: '重试 / 超时' },
  { id: 'alert',   label: '告警' },
  { id: 'deps',    label: '依赖' },
  { id: 'outputs', label: '输出变量' },
]

// Kept as JS strings so the `<` / `>` don't trip the HTML template parser.
const SAMPLE_VAR_HINT = '${biz_date} 工作流变量 · ${nodes.upstream.field} 上游节点输出'
const sampleInputRef = (input) => `\${nodes.${input}}`
const sampleOutputRef = (nodeId) => `\${nodes.${nodeId || 'X'}.<name>}`

const palette = [
  { id: 'shell',    name: 'Shell',     desc: '执行命令行' },
  { id: 'sql',      name: 'SQL',       desc: '运行 SQL 脚本' },
  { id: 'python',   name: 'Python',    desc: 'Python 脚本' },
  { id: 'http',     name: 'HTTP',      desc: 'HTTP 请求' },
  { id: 'sync',     name: '数据同步',   desc: '跨库数据集成' },
  { id: 'quality',  name: '质量检查',   desc: '断言数据规则' },
  { id: 'approval', name: '审批',       desc: '人工审批节点' },
]
</script>

<template>
  <div class="flex h-[calc(100vh-160px)] flex-col gap-3">
    <!-- header -->
    <div class="flex items-center justify-between gap-3 rounded-md border border-slate-200 bg-white px-4 py-2.5">
      <div class="flex min-w-0 items-center gap-3">
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-200 px-2.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50" @click="emit('back')">
          <span>←</span> 返回列表
        </button>
        <div class="flex min-w-0 items-center gap-2 text-sm">
          <span class="text-slate-400">数据仓库</span>
          <span class="text-slate-300">/</span>
          <span class="truncate font-semibold text-slate-800">{{ dagDetail.workflow_name }}</span>
          <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="statusMeta[dagDetail.status].pill">
            <span class="h-1.5 w-1.5 rounded-full" :class="statusMeta[dagDetail.status].dot"></span>
            {{ statusMeta[dagDetail.status].label }}
          </span>
          <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="scheduleStatusMeta[dagDetail.schedule_status].pill">
            {{ scheduleStatusMeta[dagDetail.schedule_status].label }} · {{ dagDetail.schedule }}
          </span>
        </div>
      </div>
      <div class="flex items-center gap-1.5">
        <button class="inline-flex h-8 items-center gap-1.5 rounded bg-emerald-600 px-3 text-xs font-semibold text-white transition hover:bg-emerald-700">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor"><path d="M6 4l10 6-10 6V4z"/></svg>
          立即运行
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-amber-200 bg-amber-50 px-2.5 text-xs font-semibold text-amber-800 transition hover:bg-amber-100">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor"><path d="M6 4h3v12H6zM11 4h3v12h-3z"/></svg>
          暂停调度
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-200 px-2.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50" @click="emit('open-runs', dagDetail.workflow_id)">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 4h14M3 10h14M3 16h14"/></svg>
          运行历史
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-200 px-2.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 4l2 2-9 9-3 1 1-3 9-9z"/></svg>
          编辑
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-200 px-2.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="10" cy="10" r="3"/><path d="M10 2v3M10 15v3M2 10h3M15 10h3"/></svg>
          设置
        </button>
      </div>
    </div>

    <!-- 3-pane workspace -->
    <div class="grid min-h-0 flex-1 grid-cols-[200px_minmax(0,1fr)_360px] gap-3">
      <!-- left: node palette -->
      <aside class="flex min-h-0 flex-col rounded-md border border-slate-200 bg-white">
        <div class="border-b border-slate-200 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">节点工具箱</div>
        <div class="flex-1 space-y-1 overflow-auto p-2">
          <div
            v-for="item in palette" :key="item.id"
            class="group flex cursor-grab items-center gap-2 rounded border border-transparent bg-slate-50/50 px-2 py-2 transition hover:border-slate-200 hover:bg-white active:cursor-grabbing"
            draggable="true"
          >
            <span
              class="grid h-7 w-7 shrink-0 place-items-center rounded text-[10px] font-bold text-white"
              :style="{ background: nodeTypeMeta[item.id].accent }"
            >{{ nodeTypeMeta[item.id].glyph }}</span>
            <div class="min-w-0">
              <p class="truncate text-xs font-semibold text-slate-700">{{ item.name }}</p>
              <p class="truncate text-[10px] text-slate-400">{{ item.desc }}</p>
            </div>
          </div>
          <p class="mt-3 px-1 text-[10px] leading-relaxed text-slate-400">拖拽节点到画布；连线表示依赖。Demo 中拖拽未启用。</p>
        </div>
      </aside>

      <!-- center: DAG canvas -->
      <div class="relative flex min-h-0 flex-col overflow-hidden rounded-md border border-slate-200 bg-slate-50/30">
        <!-- canvas toolbar -->
        <div class="flex items-center justify-between border-b border-slate-200 bg-white/80 px-3 py-1.5 backdrop-blur">
          <div class="flex items-center gap-3 text-[11px]">
            <span class="font-semibold text-slate-500">节点：{{ dagDetail.nodes.length }}</span>
            <span class="text-slate-300">·</span>
            <span class="font-semibold text-emerald-600">成功 {{ summary.success || 0 }}</span>
            <span class="font-semibold text-rose-600">失败 {{ summary.failed || 0 }}</span>
            <span class="font-semibold text-sky-600">运行 {{ summary.running || 0 }}</span>
            <span class="font-semibold text-slate-500">待运行 {{ summary.pending || 0 }}</span>
          </div>
          <div class="flex items-center gap-1">
            <button class="rounded p-1 text-slate-500 transition hover:bg-slate-100" title="自动布局">
              <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="4" cy="10" r="2"/><circle cx="10" cy="5" r="2"/><circle cx="10" cy="15" r="2"/><circle cx="16" cy="10" r="2"/><path d="M6 9l3-3M6 11l3 3M12 5l3 4M12 15l3-4"/></svg>
            </button>
            <button class="rounded p-1 text-slate-500 transition hover:bg-slate-100" title="居中">
              <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="14" height="14" rx="1"/><circle cx="10" cy="10" r="2"/></svg>
            </button>
            <button class="rounded p-1 text-slate-500 transition hover:bg-slate-100" title="放大">
              <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="9" r="6"/><path d="M14 14l4 4M9 6v6M6 9h6"/></svg>
            </button>
            <button class="rounded p-1 text-slate-500 transition hover:bg-slate-100" title="缩小">
              <svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="9" r="6"/><path d="M14 14l4 4M6 9h6"/></svg>
            </button>
          </div>
        </div>

        <!-- canvas viewport -->
        <div class="relative flex-1 overflow-auto">
          <div
            class="relative"
            :style="{
              width: canvas.width + 'px',
              height: canvas.height + 'px',
              backgroundImage: 'radial-gradient(circle at 1px 1px, rgb(203 213 225 / 0.6) 1px, transparent 0)',
              backgroundSize: '20px 20px',
            }"
          >
            <!-- edges (svg behind nodes) -->
            <svg
              class="pointer-events-none absolute inset-0"
              :width="canvas.width" :height="canvas.height"
              :viewBox="`0 0 ${canvas.width} ${canvas.height}`"
            >
              <defs>
                <marker id="ops-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/>
                </marker>
                <marker id="ops-arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill="#0284c7"/>
                </marker>
              </defs>
              <path
                v-for="(edge, idx) in dagDetail.edges" :key="idx"
                :d="edgePath(edge)"
                fill="none"
                :stroke="edgeHighlighted(edge) ? '#0284c7' : '#cbd5e1'"
                :stroke-width="edgeHighlighted(edge) ? 2 : 1.5"
                :marker-end="edgeHighlighted(edge) ? 'url(#ops-arrow-active)' : 'url(#ops-arrow)'"
              />
            </svg>

            <!-- nodes (absolute positioned divs over the SVG) -->
            <button
              v-for="node in dagDetail.nodes" :key="node.id"
              class="absolute flex flex-col gap-1 rounded-md border bg-white px-2.5 py-2 text-left shadow-sm transition hover:shadow-md"
              :style="{ left: node.x + 'px', top: node.y + 'px', width: NODE_W + 'px', height: NODE_H + 'px' }"
              :class="selectedNodeId === node.id ? 'border-sky-400 ring-2 ring-sky-200' : 'border-slate-200'"
              @click="selectedNodeId = node.id"
            >
              <div class="flex items-center gap-2">
                <span
                  class="grid h-5 w-5 shrink-0 place-items-center rounded text-[9px] font-bold text-white"
                  :style="{ background: nodeTypeMeta[node.type].accent }"
                >{{ nodeTypeMeta[node.type].glyph }}</span>
                <span class="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-slate-800">{{ node.name }}</span>
                <span class="h-2 w-2 shrink-0 rounded-full" :class="statusMeta[node.status].dot"></span>
              </div>
              <div class="flex items-center gap-2 text-[10.5px] text-slate-500">
                <span class="font-mono">{{ node.duration }}</span>
                <span v-if="node.retries > 0" class="rounded bg-amber-50 px-1 py-0 font-mono text-[10px] font-semibold text-amber-700 ring-1 ring-amber-200">↻{{ node.retries }}</span>
                <span class="ml-auto truncate font-mono text-slate-400">{{ node.id }}</span>
              </div>
            </button>
          </div>
        </div>

        <!-- legend -->
        <div class="flex items-center gap-3 border-t border-slate-200 bg-white px-3 py-1.5 text-[11px] text-slate-500">
          <span v-for="(meta, key) in statusMeta" :key="key" class="flex items-center gap-1.5">
            <span class="h-1.5 w-1.5 rounded-full" :class="meta.dot"></span>
            {{ meta.label }}
          </span>
        </div>
      </div>

      <!-- right: properties panel -->
      <aside class="flex min-h-0 flex-col overflow-hidden rounded-md border border-slate-200 bg-white">
        <div class="flex items-center gap-2 border-b border-slate-200 px-3 py-2.5">
          <span
            v-if="selectedNode"
            class="grid h-7 w-7 shrink-0 place-items-center rounded text-[10px] font-bold text-white"
            :style="{ background: nodeTypeMeta[selectedNode.type].accent }"
          >{{ nodeTypeMeta[selectedNode.type].glyph }}</span>
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-bold text-slate-800">{{ selectedNode?.name || '选择节点' }}</p>
            <p class="truncate text-[11px] text-slate-400 font-mono">{{ selectedNode?.id }}</p>
          </div>
          <span v-if="selectedNode" class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ring-inset" :class="statusMeta[selectedNode.status].pill">
            <span class="h-1.5 w-1.5 rounded-full" :class="statusMeta[selectedNode.status].dot"></span>
            {{ statusMeta[selectedNode.status].label }}
          </span>
        </div>
        <div class="flex border-b border-slate-200 text-[11px]">
          <button
            v-for="tab in propertyTabs" :key="tab.id"
            class="flex-1 border-b-2 px-2 py-2 font-semibold transition"
            :class="propertyTab === tab.id ? 'border-sky-500 text-sky-700' : 'border-transparent text-slate-500 hover:text-slate-700'"
            @click="propertyTab = tab.id"
          >{{ tab.label }}</button>
        </div>
        <div class="flex-1 overflow-auto p-3 text-sm">
          <div v-if="propertyTab === 'base'" class="space-y-3">
            <div>
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">名称</label>
              <input :value="selectedProps.base.name" class="mt-1 block w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm" readonly>
            </div>
            <div>
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">类型</label>
              <input :value="nodeTypeMeta[selectedNode?.type || 'shell'].label" class="mt-1 block w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm" readonly>
            </div>
            <div>
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">描述</label>
              <textarea :value="selectedProps.base.description" class="mt-1 block min-h-[60px] w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm" readonly></textarea>
            </div>
            <div>
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">资源队列</label>
              <input :value="selectedProps.queue" class="mt-1 block w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm font-mono" readonly>
            </div>
          </div>
          <div v-else-if="propertyTab === 'params'" class="space-y-2">
            <div v-for="(value, key) in selectedProps.params" :key="key">
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400 font-mono">{{ key }}</label>
              <input :value="value" class="mt-1 block w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm font-mono" readonly>
            </div>
            <div class="rounded border border-slate-200 bg-slate-50 p-2 text-[11px] text-slate-500">
              <p class="font-semibold text-slate-600">变量引用语法</p>
              <p class="mt-1 font-mono text-[10.5px]">{{ SAMPLE_VAR_HINT }}</p>
            </div>
          </div>
          <div v-else-if="propertyTab === 'retry'" class="space-y-3">
            <div class="grid grid-cols-2 gap-2">
              <div>
                <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">最大重试</label>
                <input :value="selectedProps.retry.max" class="mt-1 block w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm" readonly>
              </div>
              <div>
                <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">重试间隔</label>
                <input :value="selectedProps.retry.interval" class="mt-1 block w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm" readonly>
              </div>
            </div>
            <div>
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">失败动作</label>
              <input :value="selectedProps.retry.on_failure" class="mt-1 block w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm" readonly>
            </div>
            <div>
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">超时（秒）</label>
              <input :value="selectedProps.timeout.seconds" class="mt-1 block w-full rounded border-slate-200 bg-slate-50 px-2 py-1.5 text-sm" readonly>
            </div>
          </div>
          <div v-else-if="propertyTab === 'alert'" class="space-y-3">
            <div>
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">失败时通知</label>
              <div class="mt-1.5 flex flex-wrap gap-1.5">
                <span v-for="(target, idx) in selectedProps.alert.on_failure" :key="idx" class="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-semibold text-rose-700 ring-1 ring-inset ring-rose-200">{{ target }}</span>
              </div>
            </div>
            <div>
              <label class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">超时通知</label>
              <div class="mt-1.5 flex flex-wrap gap-1.5">
                <span v-for="(target, idx) in selectedProps.alert.on_timeout" :key="idx" class="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">{{ target }}</span>
              </div>
            </div>
            <div class="rounded border border-slate-200 bg-slate-50 p-2.5 text-[11px] text-slate-500">
              告警渠道继承项目级配置；可在「设置 → 告警」覆盖。
            </div>
          </div>
          <div v-else-if="propertyTab === 'deps'" class="space-y-2">
            <p class="text-[11px] text-slate-500">引用上游输出</p>
            <ul class="space-y-1.5">
              <li v-for="(input, idx) in selectedProps.inputs" :key="idx" class="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-[11.5px] font-mono text-slate-700">{{ sampleInputRef(input) }}</li>
              <li v-if="!selectedProps.inputs.length" class="rounded border border-dashed border-slate-200 px-2 py-3 text-center text-[11px] text-slate-400">无</li>
            </ul>
          </div>
          <div v-else-if="propertyTab === 'outputs'" class="space-y-2">
            <p class="text-[11px] text-slate-500">下游可通过 <span class="font-mono">{{ sampleOutputRef(selectedNode?.id) }}</span> 引用</p>
            <table class="w-full text-[12px]">
              <thead>
                <tr class="border-b border-slate-200">
                  <th class="py-1.5 text-left text-[10px] font-semibold uppercase tracking-wider text-slate-400">名称</th>
                  <th class="py-1.5 text-left text-[10px] font-semibold uppercase tracking-wider text-slate-400">类型</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="output in selectedProps.outputs" :key="output.name" class="border-b border-slate-100 last:border-0">
                  <td class="py-1.5 font-mono text-slate-700">{{ output.name }}</td>
                  <td class="py-1.5 font-mono text-slate-500">{{ output.type }}</td>
                </tr>
                <tr v-if="!selectedProps.outputs.length"><td colspan="2" class="py-3 text-center text-[11px] text-slate-400">无</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
