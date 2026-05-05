<script setup>
import { ref } from 'vue'
import { nodeStatusMeta } from '../../mock/workflow_meta'
import { useNoticeStore } from '../../stores/notice'

// 单个节点详情面板（运行视图右侧 main pane）。父传 node + 该节点的事件 +
// run/workflow id；重跑动作以 emit 回去，由父决定调哪个 API。
const props = defineProps({
  node:       { type: Object, required: true },
  nodeEvents: { type: Array,  default: () => [] },
  runId:      { type: String, default: '' },
  workflowId: { type: String, default: '' },
})
const emit = defineEmits(['rerun-from-node', 'rerun-defaults'])

const { copyField } = useNoticeStore()

// --- per-node 输出视图辅助 ---
const formatBytes = (n) => {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}
const formatNumber = (n) => (n == null ? '—' : n.toLocaleString('en-US'))

// artifact type → icon。type 由后端 ArtifactType 枚举给出，未知值兜底
// 显示通用文件 glyph，避免空白格。
const artifactTypeIcon = (type) => {
  if (type === 'excel') return '📊'
  if (type === 'json')  return '🧾'
  return '📦'
}

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

// 事件 meta
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
</script>

<template>
  <div class="flex flex-col rounded-xl border border-slate-200 bg-white shadow-sm">
    <div class="border-b border-slate-200 bg-slate-50/60 px-4 py-3">
      <div class="flex items-center gap-2">
        <span class="h-2.5 w-2.5 rounded-full" :class="nodeStatusMeta[node.status]?.dot"></span>
        <h3 class="text-[14px] font-bold text-slate-800">{{ node.name || node.node_id }}</h3>
        <span class="rounded px-1.5 py-0.5 text-[10px] font-mono font-bold uppercase"
              :class="{ 'bg-blue-50 text-blue-700': node.type === 'compare', 'bg-emerald-50 text-emerald-700': node.type === 'lineage', 'bg-purple-50 text-purple-700': node.type === 'http' }">{{ node.type }}</span>
        <span class="rounded-full px-2 py-0.5 text-[10.5px] font-semibold ring-1 ring-inset" :class="nodeStatusMeta[node.status]?.pill">{{ nodeStatusMeta[node.status]?.label }}</span>
        <span v-if="node.reused"
              class="rounded-full bg-amber-50 px-2 py-0.5 text-[10.5px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-200"
              title="本次没有实际执行此节点，沿用上一次 run 的输出">复用上次</span>
      </div>
      <dl class="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11.5px] md:grid-cols-4">
        <div><dt class="text-slate-400">开始</dt><dd class="font-mono text-slate-700">{{ node.started_at || '—' }}</dd></div>
        <div><dt class="text-slate-400">结束</dt><dd class="font-mono text-slate-700">{{ node.finished_at || '—' }}</dd></div>
        <div><dt class="text-slate-400">耗时</dt><dd class="font-mono text-slate-700">{{ node.elapsed_seconds }}s</dd></div>
        <div><dt class="text-slate-400">node_id</dt><dd class="font-mono text-slate-700">{{ node.node_id }}</dd></div>
      </dl>
    </div>

    <div class="flex-1 overflow-auto">
      <!-- 错误信息（如有） -->
      <div v-if="node.error" class="border-b border-rose-200 bg-rose-50 px-4 py-3">
        <p class="text-[11px] font-bold uppercase tracking-wider text-rose-700">错误</p>
        <pre class="mt-1 whitespace-pre-wrap font-mono text-[12px] text-rose-900">{{ node.error }}</pre>
        <div class="mt-2 flex gap-2">
          <button class="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
                  @click="copyField(node.error)">
            ⎘ 复制错误
          </button>
          <button v-if="runId"
                  class="inline-flex h-7 items-center gap-1 rounded-lg bg-rose-600 px-2.5 text-[11px] font-semibold text-white transition hover:bg-rose-700"
                  title="从此节点开始重跑：上游沿用本次输出，此节点及其下游重新执行；变量沿用本次"
                  @click="emit('rerun-from-node', node.node_id)">
            ↻ 从此节点重跑
          </button>
          <button v-if="workflowId"
                  class="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
                  title="按当前作业流配置 + 默认变量重跑全部节点"
                  @click="emit('rerun-defaults')">
            ↻ 全量重跑
          </button>
        </div>
      </div>

      <!-- 产物（artifacts）—— 任意节点产生的可下载文件统一在这里展示。
           老节点（compare）的 excel_filename / result_filename 还没收口到
           artifacts 列表，下面 compare 区块仍保留内联下载按钮兜底。 -->
      <div v-if="(node.output && (node.output.artifacts || []).length)" class="border-b border-slate-100 bg-emerald-50/30 px-4 py-3">
        <p class="text-[11px] font-bold uppercase tracking-wider text-emerald-700">产物 ({{ node.output.artifacts.length }})</p>
        <ul class="mt-2 space-y-1.5">
          <li v-for="art in node.output.artifacts" :key="art.id"
              class="flex items-center gap-2 rounded-lg border border-emerald-200 bg-white px-3 py-2">
            <span class="text-[14px]">{{ artifactTypeIcon(art.type) }}</span>
            <div class="min-w-0 flex-1">
              <p class="truncate font-mono text-[12px] font-semibold text-slate-800">{{ art.name }}</p>
              <p class="text-[10.5px] text-slate-500">
                <span class="rounded bg-slate-100 px-1 py-0.5 font-mono text-[9.5px] uppercase">{{ art.type }}</span>
                <span v-if="art.description"> · {{ art.description }}</span>
                <span v-if="art.size_bytes"> · {{ formatBytes(art.size_bytes) }}</span>
              </p>
            </div>
            <a :href="`/results/${art.relative_path}`" target="_blank"
               class="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg bg-emerald-600 px-2.5 text-[11px] font-semibold text-white transition hover:bg-emerald-700">
              ⬇ 下载
            </a>
          </li>
        </ul>
      </div>

      <!-- 节点输出（按 type 结构化） -->
      <div v-if="node.output && Object.keys(node.output).length" class="border-b border-slate-100 px-4 py-3">

        <!-- compare 节点：4 个统计卡 + 任务文件下载 + samples 预览 -->
        <div v-if="node.type === 'compare' && node.output.summary">
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">数据对比结果</p>
          <div class="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
            <div v-for="b in compareBuckets" :key="b.key" class="rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
              <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{{ b.label }}</p>
              <p class="mt-0.5 font-mono text-xl font-bold tabular-nums" :class="b.textClass">{{ formatNumber(node.output.summary[b.key]) }}</p>
            </div>
          </div>
          <div class="mt-2 flex flex-wrap items-center gap-2 text-[11.5px] text-slate-600">
            <span>源端 <span class="font-mono font-semibold text-slate-700">{{ formatNumber(node.output.source_rows) }}</span> 行</span>
            <span class="text-slate-300">·</span>
            <span>目标 <span class="font-mono font-semibold text-slate-700">{{ formatNumber(node.output.target_rows) }}</span> 行</span>
            <span v-if="node.output.task_name" class="text-slate-300">·</span>
            <span v-if="node.output.task_name">任务 <span class="font-mono text-slate-700">{{ node.output.task_name }}</span></span>
          </div>
          <div v-if="node.output.excel_filename || node.output.result_filename" class="mt-2 flex flex-wrap gap-2">
            <a v-if="node.output.excel_filename"
               :href="`/results/${node.output.excel_filename}`"
               target="_blank"
               class="inline-flex h-7 items-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 text-[11px] font-semibold text-emerald-700 transition hover:bg-emerald-100">
              ⬇ Excel 结果
            </a>
            <a v-if="node.output.result_filename"
               :href="`/results/${node.output.result_filename}`"
               target="_blank"
               class="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">
              ⬇ JSON
            </a>
          </div>
          <!-- samples 预览（最多 5 行） -->
          <div v-if="compareSamplePreview(node.output)" class="mt-3 rounded-lg border border-slate-200">
            <div class="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-1.5">
              <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">{{ compareSamplePreview(node.output).label }} 预览（前 5 行 / 共 {{ compareSamplePreview(node.output).total }}）</span>
            </div>
            <table class="w-full text-[11.5px]">
              <thead class="bg-slate-50/60">
                <tr><th class="px-3 py-1.5 text-left font-semibold text-slate-500 w-[180px]">主键</th><th class="px-3 py-1.5 text-left font-semibold text-slate-500">{{ compareSamplePreview(node.output).key === 'diff' ? '字段差异' : '行内容' }}</th></tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr v-for="(row, i) in compareSamplePreview(node.output).rows" :key="i">
                  <td class="px-3 py-1 font-mono text-[10.5px] text-slate-700 whitespace-nowrap">{{ flattenSampleRow(row, compareSamplePreview(node.output).key).keyText }}</td>
                  <td class="px-3 py-1 font-mono text-[10.5px] text-slate-700 break-all">{{ flattenSampleRow(row, compareSamplePreview(node.output).key).payload }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- excel_export 节点：sheet 表（文件本身在上面 artifacts 面板下载） -->
        <div v-else-if="node.type === 'excel_export'">
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">Excel 导出 · sheet 详情</p>
          <p class="mt-1 text-[10.5px] text-slate-500">
            {{ node.output.sheet_count }} 个 sheet · 共
            <span class="font-mono">{{ formatNumber(node.output.total_rows_written) }}</span> 行
          </p>
          <div v-if="(node.output.sheets || []).length" class="mt-2 rounded-lg border border-slate-200">
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
                <template v-for="(sh, i) in node.output.sheets" :key="i">
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
        <div v-else-if="node.type === 'params'">
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">参数解析（{{ Object.keys(node.output).length }} 个）</p>
          <div class="mt-2 rounded-lg border border-slate-200">
            <table class="w-full text-[11.5px]">
              <thead class="bg-slate-50/60">
                <tr><th class="px-3 py-1.5 text-left font-semibold text-slate-500 w-[160px]">参数</th><th class="px-3 py-1.5 text-left font-semibold text-slate-500">值</th></tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr v-for="(value, name) in node.output" :key="name">
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
        <div v-else-if="node.type === 'http'">
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">HTTP 响应</p>
          <div class="mt-2 flex items-center gap-3 text-[12px]">
            <span class="rounded px-2 py-0.5 font-mono font-bold ring-1 ring-inset"
                  :class="node.output.status >= 200 && node.output.status < 300 ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : 'bg-rose-50 text-rose-700 ring-rose-200'">
              {{ node.output.status }}
            </span>
            <span v-if="node.output.truncated" class="text-[10.5px] text-amber-700">⚠ 响应体已截断（256 KB）</span>
          </div>
          <pre v-if="node.output.body" class="mt-2 max-h-48 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-[11px] leading-relaxed text-slate-700">{{ node.output.body }}</pre>
        </div>

        <!-- lineage 节点：source/target 计数 + warnings -->
        <div v-else-if="node.type === 'lineage'">
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">血缘分析</p>
          <div class="mt-2 grid grid-cols-3 gap-2">
            <div class="rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
              <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">来源表</p>
              <p class="mt-0.5 font-mono text-lg font-bold tabular-nums text-slate-700">{{ formatNumber((node.output.sources || []).length) }}</p>
            </div>
            <div class="rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
              <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">目标表</p>
              <p class="mt-0.5 font-mono text-lg font-bold tabular-nums text-slate-700">{{ formatNumber((node.output.targets || []).length) }}</p>
            </div>
            <div class="rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2">
              <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">血缘边</p>
              <p class="mt-0.5 font-mono text-lg font-bold tabular-nums text-slate-700">{{ formatNumber((node.output.edges || []).length) }}</p>
            </div>
          </div>
          <div v-if="(node.output.warnings || []).length" class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
            <p class="text-[10.5px] font-semibold uppercase tracking-wider text-amber-700">⚠ {{ node.output.warnings.length }} 个警告</p>
            <ul class="mt-1 space-y-0.5 text-[11px] text-amber-900">
              <li v-for="(w, i) in node.output.warnings.slice(0, 5)" :key="i" class="font-mono">{{ typeof w === 'string' ? w : JSON.stringify(w) }}</li>
            </ul>
          </div>
        </div>

        <!-- 未知类型：fallback 到 JSON -->
        <div v-else>
          <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">节点输出 ({{ Object.keys(node.output).length }} keys)</p>
          <pre class="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ JSON.stringify(node.output, null, 2) }}</pre>
        </div>

        <!-- 折叠的原始 JSON（任何 type 都给一个逃生通道）-->
        <div class="mt-3">
          <button class="text-[10.5px] font-mono text-slate-500 transition hover:text-slate-700"
                  @click="toggleRawJson(node.node_id)">
            {{ showRawJson[node.node_id] ? '▾' : '▸' }} 原始 output JSON
          </button>
          <pre v-if="showRawJson[node.node_id]" class="mt-1 max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ JSON.stringify(node.output, null, 2) }}</pre>
        </div>

        <p class="mt-2 font-mono text-[10.5px] text-slate-400">下游可通过 <code>{{ '${nodes.' + node.node_id + '.<path>}' }}</code> 引用</p>
      </div>

      <!-- 该节点的事件 -->
      <div class="px-4 py-3">
        <p class="text-[11px] font-bold uppercase tracking-wider text-slate-500">本节点事件 ({{ nodeEvents.length }})</p>
        <div v-if="!nodeEvents.length" class="mt-2 rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-[11.5px] text-slate-400">无事件</div>
        <div v-else class="mt-2 font-mono text-[12px]">
          <div v-for="(ev, idx) in nodeEvents" :key="idx" class="grid grid-cols-[120px_1fr] items-start gap-3 border-b border-slate-100 py-1.5 last:border-0">
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
  </div>
</template>
