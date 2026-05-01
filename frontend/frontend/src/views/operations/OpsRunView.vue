<script setup>
import { computed, ref } from 'vue'
import { dagDetail, runInstances, statusMeta, nodeTypeMeta, logSamples } from '../../mock/operations'

const emit = defineEmits(['back', 'open-dag'])

const selectedRunId = ref(runInstances[0].id)   // most recent run = the failed one
const selectedNodeId = ref('transform')
const logTab = ref('stdout')   // stdout / stderr / retries / metrics
const logSearch = ref('')

const selectedRun = computed(() => runInstances.find((r) => r.id === selectedRunId.value))

// Decorate DAG nodes with the run-specific status from the selected run.
const runNodes = computed(() => {
  const run = selectedRun.value
  if (!run) return []
  // For demo purposes, the run summary tells us how many of each. We just
  // walk the original DAG and use the node's status (mock data is consistent).
  return dagDetail.nodes.map((node) => ({ ...node, log: logSamples[node.id] }))
})

const selectedNode = computed(() => runNodes.value.find((n) => n.id === selectedNodeId.value))
const selectedLog = computed(() => logSamples[selectedNodeId.value] || null)

const filteredStdout = computed(() => {
  if (!selectedLog.value) return []
  if (!logSearch.value.trim()) return selectedLog.value.stdout
  const term = logSearch.value.trim().toLowerCase()
  return selectedLog.value.stdout.filter((line) => line.toLowerCase().includes(term))
})

const filteredStderr = computed(() => {
  if (!selectedLog.value) return []
  if (!logSearch.value.trim()) return selectedLog.value.stderr
  const term = logSearch.value.trim().toLowerCase()
  return selectedLog.value.stderr.filter((line) => line.toLowerCase().includes(term))
})

const copyLog = async () => {
  if (!selectedLog.value) return
  const lines = logTab.value === 'stderr' ? selectedLog.value.stderr : selectedLog.value.stdout
  try {
    await navigator.clipboard.writeText(lines.join('\n'))
  } catch (e) { /* prototype: ignore */ }
}
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
          <button class="font-semibold text-slate-800 transition hover:text-sky-700" @click="emit('open-dag', dagDetail.workflow_id)">{{ dagDetail.workflow_name }}</button>
          <span class="text-slate-300">/</span>
          <span class="font-mono text-[12px] text-slate-600">{{ selectedRun?.id }}</span>
          <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="statusMeta[selectedRun?.status || 'pending'].pill">
            <span class="h-1.5 w-1.5 rounded-full" :class="statusMeta[selectedRun?.status || 'pending'].dot"></span>
            {{ statusMeta[selectedRun?.status || 'pending'].label }}
          </span>
        </div>
      </div>
      <div class="flex items-center gap-1.5">
        <button class="inline-flex h-8 items-center gap-1.5 rounded bg-amber-600 px-3 text-xs font-semibold text-white transition hover:bg-amber-700">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8a7 7 0 0114-1M17 12a7 7 0 01-14 1M3 4v4h4M17 16v-4h-4"/></svg>
          重跑失败节点
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-200 px-2.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8a7 7 0 0114-1M17 12a7 7 0 01-14 1M3 4v4h4M17 16v-4h-4"/></svg>
          重跑全部
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-rose-200 bg-rose-50 px-2.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor"><rect x="5" y="5" width="10" height="10" rx="1"/></svg>
          停止
        </button>
        <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-200 px-2.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50" @click="emit('open-dag', dagDetail.workflow_id)">
          <svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="4" cy="10" r="2"/><circle cx="10" cy="5" r="2"/><circle cx="10" cy="15" r="2"/><circle cx="16" cy="10" r="2"/><path d="M6 9l3-3M6 11l3 3M12 5l3 4M12 15l3-4"/></svg>
          DAG 视图
        </button>
      </div>
    </div>

    <!-- 3-pane workspace -->
    <div class="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)_280px] gap-3">
      <!-- left: node list ordered by DAG -->
      <aside class="flex min-h-0 flex-col rounded-md border border-slate-200 bg-white">
        <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
          <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">节点 ({{ runNodes.length }})</span>
          <span class="text-[11px] text-slate-500 font-mono">耗时 {{ selectedRun?.duration }}</span>
        </div>
        <div class="flex-1 overflow-auto">
          <button
            v-for="node in runNodes" :key="node.id"
            class="flex w-full items-center gap-2.5 border-b border-slate-100 px-3 py-2.5 text-left transition last:border-0"
            :class="selectedNodeId === node.id ? 'bg-sky-50/70' : 'hover:bg-slate-50'"
            @click="selectedNodeId = node.id"
          >
            <span
              class="grid h-6 w-6 shrink-0 place-items-center rounded text-[9px] font-bold text-white"
              :style="{ background: nodeTypeMeta[node.type].accent }"
            >{{ nodeTypeMeta[node.type].glyph }}</span>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5">
                <span class="truncate text-[12.5px] font-semibold text-slate-800">{{ node.name }}</span>
                <span v-if="node.retries > 0" class="rounded bg-amber-50 px-1 text-[9.5px] font-bold text-amber-700 ring-1 ring-amber-200">↻{{ node.retries }}</span>
              </div>
              <p class="mt-0.5 flex items-center gap-2 text-[10.5px] text-slate-500">
                <span class="font-mono">{{ node.duration }}</span>
                <span class="truncate font-mono text-slate-400">{{ node.id }}</span>
              </p>
            </div>
            <span class="h-2 w-2 shrink-0 rounded-full" :class="statusMeta[node.status].dot"></span>
          </button>
        </div>
      </aside>

      <!-- center: log viewer -->
      <div class="flex min-h-0 flex-col overflow-hidden rounded-md border border-slate-200 bg-white">
        <!-- node summary -->
        <div class="border-b border-slate-200 bg-slate-50/60 px-4 py-3">
          <div class="flex items-center gap-2">
            <span
              class="grid h-7 w-7 shrink-0 place-items-center rounded text-[10px] font-bold text-white"
              :style="{ background: nodeTypeMeta[selectedNode?.type || 'shell'].accent }"
            >{{ nodeTypeMeta[selectedNode?.type || 'shell'].glyph }}</span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-bold text-slate-800">{{ selectedNode?.name }}</p>
              <p class="truncate text-[11px] text-slate-500 font-mono">{{ selectedNode?.id }} · {{ nodeTypeMeta[selectedNode?.type || 'shell'].label }}</p>
            </div>
            <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="statusMeta[selectedNode?.status || 'pending'].pill">
              <span class="h-1.5 w-1.5 rounded-full" :class="statusMeta[selectedNode?.status || 'pending'].dot"></span>
              {{ statusMeta[selectedNode?.status || 'pending'].label }}
            </span>
          </div>
          <dl v-if="selectedLog" class="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11.5px] md:grid-cols-4 lg:grid-cols-6">
            <div><dt class="text-slate-400">开始</dt><dd class="font-mono text-slate-700">{{ selectedLog.summary.started }}</dd></div>
            <div><dt class="text-slate-400">结束</dt><dd class="font-mono text-slate-700">{{ selectedLog.summary.ended }}</dd></div>
            <div><dt class="text-slate-400">耗时</dt><dd class="font-mono text-slate-700">{{ selectedLog.summary.duration }}</dd></div>
            <div><dt class="text-slate-400">重试</dt><dd class="font-mono" :class="selectedLog.summary.retries > 0 ? 'text-amber-700 font-semibold' : 'text-slate-700'">{{ selectedLog.summary.retries }}</dd></div>
            <div><dt class="text-slate-400">主机</dt><dd class="font-mono text-slate-700">{{ selectedLog.summary.host }}</dd></div>
            <div><dt class="text-slate-400">队列</dt><dd class="font-mono text-slate-700">{{ selectedLog.summary.queue }}</dd></div>
          </dl>
        </div>

        <!-- log tabs + toolbar -->
        <div class="flex items-center gap-3 border-b border-slate-200 bg-white px-3 py-1.5">
          <div class="flex text-[11.5px]">
            <button
              class="border-b-2 px-3 py-1.5 font-semibold transition"
              :class="logTab === 'stdout' ? 'border-sky-500 text-sky-700' : 'border-transparent text-slate-500 hover:text-slate-700'"
              @click="logTab = 'stdout'"
            >
              stdout
              <span class="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500">{{ selectedLog?.stdout.length || 0 }}</span>
            </button>
            <button
              class="border-b-2 px-3 py-1.5 font-semibold transition"
              :class="logTab === 'stderr' ? 'border-sky-500 text-sky-700' : 'border-transparent text-slate-500 hover:text-slate-700'"
              @click="logTab = 'stderr'"
            >
              stderr
              <span v-if="selectedLog?.stderr.length" class="ml-1 rounded bg-rose-50 px-1 text-[10px] font-bold text-rose-700 ring-1 ring-rose-200">{{ selectedLog.stderr.length }}</span>
            </button>
            <button
              class="border-b-2 px-3 py-1.5 font-semibold transition"
              :class="logTab === 'retries' ? 'border-sky-500 text-sky-700' : 'border-transparent text-slate-500 hover:text-slate-700'"
              @click="logTab = 'retries'"
            >
              重试记录
              <span v-if="selectedLog?.retries.length" class="ml-1 rounded bg-amber-50 px-1 text-[10px] font-bold text-amber-700 ring-1 ring-amber-200">{{ selectedLog.retries.length }}</span>
            </button>
          </div>
          <div class="ml-auto flex items-center gap-2">
            <input v-model="logSearch" placeholder="搜索日志..." class="h-7 w-48 rounded border-slate-300 bg-white px-2 text-[12px]">
            <button class="inline-flex h-7 items-center gap-1.5 rounded border border-slate-200 px-2 text-[11px] font-semibold text-slate-700 transition hover:bg-slate-50" @click="copyLog">
              <svg class="h-3 w-3" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="6" width="11" height="11" rx="1"/><path d="M3 13V4a1 1 0 011-1h9"/></svg>
              复制
            </button>
            <button class="inline-flex h-7 items-center gap-1.5 rounded border border-slate-200 px-2 text-[11px] font-semibold text-slate-700 transition hover:bg-slate-50">
              <svg class="h-3 w-3" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 4v9m0 0l-3-3m3 3l3-3M3 16h14"/></svg>
              下载
            </button>
          </div>
        </div>

        <!-- log content -->
        <div class="min-h-0 flex-1 overflow-auto bg-slate-950 font-mono text-[12px] leading-relaxed text-slate-200">
          <template v-if="logTab === 'stdout'">
            <div v-if="!filteredStdout.length" class="px-4 py-8 text-center text-slate-500">{{ logSearch ? '没有匹配的日志行' : '无 stdout 输出' }}</div>
            <div v-for="(line, idx) in filteredStdout" :key="idx" class="flex gap-4 px-4 py-0.5 hover:bg-slate-900">
              <span class="w-10 shrink-0 select-none text-right text-slate-600">{{ idx + 1 }}</span>
              <span class="flex-1 whitespace-pre-wrap break-all">{{ line }}</span>
            </div>
          </template>
          <template v-else-if="logTab === 'stderr'">
            <div v-if="!filteredStderr.length" class="px-4 py-8 text-center text-slate-500">{{ logSearch ? '没有匹配的日志行' : '无 stderr 输出' }}</div>
            <div v-for="(line, idx) in filteredStderr" :key="idx" class="flex gap-4 px-4 py-0.5 text-rose-200 hover:bg-slate-900">
              <span class="w-10 shrink-0 select-none text-right text-slate-600">{{ idx + 1 }}</span>
              <span class="flex-1 whitespace-pre-wrap break-all">{{ line }}</span>
            </div>
          </template>
          <template v-else-if="logTab === 'retries'">
            <div v-if="!selectedLog?.retries.length" class="px-4 py-8 text-center text-slate-500">没有重试记录</div>
            <div v-for="retry in selectedLog?.retries || []" :key="retry.idx" class="border-b border-slate-800 px-4 py-2.5 last:border-0">
              <div class="flex items-center gap-3">
                <span class="rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] font-bold text-amber-300 ring-1 ring-amber-800">第 {{ retry.idx + 1 }} 次</span>
                <span class="text-slate-300">{{ retry.started }} → {{ retry.ended }}</span>
                <span class="rounded-full px-2 py-0.5 text-[10px] font-bold" :class="retry.status === 'success' ? 'bg-emerald-900/40 text-emerald-300 ring-1 ring-emerald-800' : 'bg-rose-900/40 text-rose-300 ring-1 ring-rose-800'">{{ retry.status }}</span>
              </div>
              <p class="mt-1 text-rose-200">{{ retry.error }}</p>
            </div>
          </template>
        </div>
      </div>

      <!-- right: run sidecar (instance + history) -->
      <aside class="flex min-h-0 flex-col gap-3 overflow-hidden">
        <!-- instance info -->
        <div class="rounded-md border border-slate-200 bg-white">
          <div class="border-b border-slate-200 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">运行实例</div>
          <div class="space-y-2.5 p-3 text-[12px]">
            <div class="flex justify-between gap-2"><span class="text-slate-500">实例 ID</span><span class="font-mono text-slate-700">{{ selectedRun?.id }}</span></div>
            <div class="flex justify-between gap-2"><span class="text-slate-500">触发方式</span><span class="text-slate-700">{{ selectedRun?.triggered_by }}</span></div>
            <div class="flex justify-between gap-2"><span class="text-slate-500">执行人</span><span class="text-slate-700">{{ selectedRun?.operator }}</span></div>
            <div class="flex justify-between gap-2"><span class="text-slate-500">开始</span><span class="font-mono text-slate-700">{{ selectedRun?.started_at }}</span></div>
            <div class="flex justify-between gap-2"><span class="text-slate-500">结束</span><span class="font-mono text-slate-700">{{ selectedRun?.ended_at }}</span></div>
            <div class="flex justify-between gap-2"><span class="text-slate-500">耗时</span><span class="font-mono text-slate-700">{{ selectedRun?.duration }}</span></div>
            <div class="border-t border-slate-100 pt-2.5">
              <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">节点统计</p>
              <div class="grid grid-cols-2 gap-1.5">
                <div class="rounded bg-emerald-50 px-2 py-1.5"><p class="text-[10px] text-emerald-700">成功</p><p class="font-mono text-base font-bold text-emerald-700">{{ selectedRun?.summary.success }}</p></div>
                <div class="rounded bg-rose-50 px-2 py-1.5"><p class="text-[10px] text-rose-700">失败</p><p class="font-mono text-base font-bold text-rose-700">{{ selectedRun?.summary.failed }}</p></div>
                <div class="rounded bg-sky-50 px-2 py-1.5"><p class="text-[10px] text-sky-700">运行中</p><p class="font-mono text-base font-bold text-sky-700">{{ selectedRun?.summary.running }}</p></div>
                <div class="rounded bg-slate-100 px-2 py-1.5"><p class="text-[10px] text-slate-600">未运行</p><p class="font-mono text-base font-bold text-slate-700">{{ selectedRun?.summary.pending }}</p></div>
              </div>
            </div>
          </div>
        </div>

        <!-- run history -->
        <div class="flex min-h-0 flex-1 flex-col rounded-md border border-slate-200 bg-white">
          <div class="border-b border-slate-200 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">历史运行</div>
          <div class="flex-1 overflow-auto">
            <button
              v-for="run in runInstances" :key="run.id"
              class="flex w-full items-center gap-2 border-b border-slate-100 px-3 py-2 text-left transition last:border-0"
              :class="selectedRunId === run.id ? 'bg-sky-50/70' : 'hover:bg-slate-50'"
              @click="selectedRunId = run.id"
            >
              <span class="h-2 w-2 shrink-0 rounded-full" :class="statusMeta[run.status].dot"></span>
              <div class="min-w-0 flex-1">
                <p class="truncate text-[11.5px] font-mono text-slate-700">{{ run.id.slice(0, 13) }}</p>
                <p class="text-[10.5px] text-slate-500">{{ run.started_at }} · {{ run.duration }}</p>
              </div>
              <span class="text-[10.5px] text-slate-400">{{ run.triggered_by }}</span>
            </button>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
