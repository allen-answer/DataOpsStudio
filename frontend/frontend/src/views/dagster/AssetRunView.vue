<script setup>
import { computed, ref } from 'vue'
import { focalRun, runSteps, runEvents } from '../../mock/dagster'

const emit = defineEmits(['back'])

const eventFilter = ref('all')   // all / mat / check / log / error
const stepFilter = ref('')        // selected step name; '' = all

const filteredEvents = computed(() => {
  return runEvents.filter((ev) => {
    if (stepFilter.value && ev.step !== stepFilter.value) return false
    if (eventFilter.value === 'mat'   && ev.type !== 'MATERIALIZATION') return false
    if (eventFilter.value === 'check' && ev.type !== 'EXPECTATION_RESULT') return false
    if (eventFilter.value === 'log'   && !['LOG', 'STEP_START', 'STEP_SUCCESS'].includes(ev.type)) return false
    if (eventFilter.value === 'error' && ev.level !== 'ERROR' && ev.type !== 'STEP_FAILURE' && ev.type !== 'RUN_FAILURE') return false
    return true
  })
})

const totalDurationS = computed(() => {
  let max = 0
  for (const step of runSteps) {
    const end = step.start_offset_s + step.duration_s
    if (end > max) max = end
  }
  return Math.max(max, 1)
})

const stepBarStyle = (step) => ({
  left:  `${(step.start_offset_s / totalDurationS.value) * 100}%`,
  width: `${(step.duration_s / totalDurationS.value) * 100}%`,
})

const stepStatusClass = (status) => ({
  success: 'bg-emerald-500',
  failed:  'bg-rose-500',
  running: 'bg-blue-500 animate-pulse',
  skipped: 'bg-slate-300',
}[status] || 'bg-slate-300')

const eventTypeMeta = {
  RUN_START:           { glyph: '▶', text: 'text-blue-600',    label: '运行开始' },
  RUN_FAILURE:         { glyph: '✕', text: 'text-rose-600',    label: '运行失败' },
  STEP_START:          { glyph: '·', text: 'text-slate-500',   label: '步骤开始' },
  STEP_SUCCESS:        { glyph: '✓', text: 'text-emerald-600', label: '步骤成功' },
  STEP_FAILURE:        { glyph: '✕', text: 'text-rose-600',    label: '步骤失败' },
  MATERIALIZATION:     { glyph: '◈', text: 'text-blue-600',    label: '物化' },
  EXPECTATION_RESULT:  { glyph: '✓', text: 'text-emerald-600', label: '检查结果' },
  LOG:                 { glyph: '›', text: 'text-slate-500',   label: '日志' },
}

const levelClass = (level) => ({
  INFO:  'text-slate-600',
  WARN:  'text-amber-700',
  ERROR: 'text-rose-700',
}[level] || 'text-slate-600')

const matEvents  = computed(() => runEvents.filter((e) => e.type === 'MATERIALIZATION'))
const checkEvents = computed(() => runEvents.filter((e) => e.type === 'EXPECTATION_RESULT'))
const errorEvents = computed(() => runEvents.filter((e) => e.level === 'ERROR' || e.type === 'STEP_FAILURE'))

const statusPill = (status) => ({
  success: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  failed:  'bg-rose-50 text-rose-700 ring-rose-200',
  running: 'bg-blue-50 text-blue-700 ring-blue-200',
}[status] || 'bg-slate-100 text-slate-500 ring-slate-200')

const statusLabel = (status) => ({
  success: '成功', failed: '失败', running: '运行中',
}[status] || status)
</script>

<template>
  <div class="flex h-[calc(100vh-200px)] flex-col gap-3">
    <!-- header -->
    <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-[11px] text-slate-500">
            <button class="text-slate-600 transition hover:text-blue-600" @click="emit('back')">← 资产图谱</button>
            <span class="text-slate-300">/</span>
            <span>运行</span>
            <span class="text-slate-300">/</span>
            <span class="font-mono">{{ focalRun.id }}</span>
          </div>
          <div class="mt-1 flex items-center gap-2">
            <span class="rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset" :class="statusPill(focalRun.status)">{{ statusLabel(focalRun.status) }}</span>
            <span class="font-mono text-[14px] text-slate-700">{{ focalRun.triggered_by }}</span>
            <span class="text-slate-300">·</span>
            <span class="font-mono text-[12px] text-slate-500">分区 {{ focalRun.partition }}</span>
            <span class="text-slate-300">·</span>
            <span class="font-mono text-[12px] text-slate-500">{{ focalRun.duration }}</span>
          </div>
          <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
            <span v-for="(value, key) in focalRun.tags" :key="key" class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px]">
              <span class="text-slate-500">{{ key }}=</span><span class="text-slate-700">{{ value }}</span>
            </span>
            <span class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ focalRun.code_location }}</span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-amber-600 px-3 text-xs font-semibold text-white transition hover:bg-amber-700">
            ⟳ 重跑失败步骤
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">
            重跑全部
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-2.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100">
            ▣ 终止
          </button>
        </div>
      </div>

      <!-- run summary stats -->
      <div class="mt-3 grid grid-cols-2 gap-2 md:grid-cols-5">
        <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">开始时间</p>
          <p class="mt-0.5 font-mono text-[12.5px] text-slate-700">{{ focalRun.started_at }}</p>
        </div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">结束时间</p>
          <p class="mt-0.5 font-mono text-[12.5px] text-slate-700">{{ focalRun.ended_at }}</p>
        </div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">步骤</p>
          <p class="mt-0.5 font-mono text-[12.5px]"><span class="text-emerald-600 font-semibold">{{ focalRun.step_summary.success }}</span> / <span class="text-rose-600 font-semibold">{{ focalRun.step_summary.failed }}</span> / <span class="text-slate-600">{{ focalRun.step_summary.total }}</span></p>
        </div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">物化次数</p>
          <p class="mt-0.5 font-mono text-[12.5px] font-semibold text-blue-600">{{ matEvents.length }}</p>
        </div>
        <div class="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">错误数</p>
          <p class="mt-0.5 font-mono text-[12.5px] font-semibold" :class="errorEvents.length > 0 ? 'text-rose-700' : 'text-slate-700'">{{ errorEvents.length }}</p>
        </div>
      </div>
    </div>

    <!-- gantt-like step timeline -->
    <div class="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">步骤时间线 · 共 {{ totalDurationS }} 秒</span>
        <button v-if="stepFilter" class="text-[10.5px] font-semibold text-blue-600 hover:text-blue-700" @click="stepFilter = ''">清除筛选 ✕</button>
      </div>
      <div class="space-y-1.5 p-3">
        <button
          v-for="step in runSteps" :key="step.name"
          class="grid w-full grid-cols-[180px_minmax(0,1fr)_64px] items-center gap-3 rounded-lg px-2 py-1.5 text-left transition"
          :class="stepFilter === step.name ? 'bg-blue-50 ring-1 ring-blue-200' : 'hover:bg-slate-50'"
          @click="stepFilter = stepFilter === step.name ? '' : step.name"
        >
          <span class="truncate font-mono text-[11.5px] text-slate-600">{{ step.asset }}</span>
          <span class="relative h-3 rounded-full bg-slate-100">
            <span class="absolute top-0 h-3 rounded-full" :class="stepStatusClass(step.status)" :style="stepBarStyle(step)"></span>
          </span>
          <span class="text-right font-mono text-[10.5px] text-slate-500">{{ step.duration_s }}秒</span>
        </button>
      </div>
    </div>

    <!-- event stream + side panel -->
    <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_320px] gap-3 overflow-hidden">
      <!-- event stream -->
      <div class="flex min-h-0 flex-col rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div class="flex items-center gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-1.5">
          <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">事件流</span>
          <span class="text-slate-300">·</span>
          <span class="font-mono text-[10.5px] text-slate-500">{{ filteredEvents.length }} / {{ runEvents.length }}</span>
          <div class="ml-auto flex items-center gap-1">
            <button
              v-for="key in ['all','mat','check','log','error']" :key="key"
              class="rounded-lg border px-2 py-0.5 text-[10.5px] transition"
              :class="eventFilter === key ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700'"
              @click="eventFilter = key"
            >{{ { all: '全部', mat: '物化', check: '检查', log: '日志', error: '错误' }[key] }}</button>
          </div>
        </div>
        <div class="min-h-0 flex-1 overflow-auto font-mono text-[12px]">
          <div v-if="!filteredEvents.length" class="px-4 py-8 text-center text-slate-400">没有匹配当前筛选的事件</div>
          <div v-for="(ev, idx) in filteredEvents" :key="idx" class="grid grid-cols-[120px_140px_1fr] items-start gap-3 border-b border-slate-100 px-3 py-1.5 last:border-0 transition hover:bg-slate-50">
            <span class="font-mono text-[10.5px] text-slate-400">{{ ev.ts }}</span>
            <span class="flex items-center gap-1.5 truncate">
              <span class="grid h-4 w-4 shrink-0 place-items-center text-[11px]" :class="eventTypeMeta[ev.type]?.text || 'text-slate-500'">{{ eventTypeMeta[ev.type]?.glyph || '·' }}</span>
              <span class="truncate text-[10.5px] font-bold uppercase tracking-wider" :class="eventTypeMeta[ev.type]?.text || 'text-slate-500'">{{ eventTypeMeta[ev.type]?.label || ev.type }}</span>
            </span>
            <div class="min-w-0">
              <p class="break-words" :class="levelClass(ev.level)">{{ ev.msg }}</p>
              <p v-if="ev.step" class="mt-0.5 text-[10.5px] text-slate-400">步骤={{ ev.step }}</p>
              <div v-if="ev.metadata" class="mt-1 flex flex-wrap gap-1">
                <span v-for="(value, key) in ev.metadata" :key="key" class="rounded bg-slate-100 px-1.5 py-0.5 text-[10.5px]">
                  <span class="text-slate-500">{{ key }}=</span><span class="text-slate-700">{{ value }}</span>
                </span>
              </div>
              <pre v-if="ev.stack" class="mt-1.5 overflow-x-auto rounded-lg border border-rose-200 bg-rose-50 p-2 text-[11px] leading-relaxed text-rose-900">{{ ev.stack.join('\n') }}</pre>
            </div>
          </div>
        </div>
      </div>

      <!-- side panel: run sidebar -->
      <aside class="flex min-h-0 flex-col gap-3 overflow-hidden">
        <div class="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">运行摘要</p>
          <dl class="space-y-1.5 text-[12px]">
            <div class="flex justify-between gap-2"><dt class="text-slate-500">运行 ID</dt><dd class="font-mono text-slate-700">{{ focalRun.id }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">状态</dt><dd><span class="rounded-full px-1.5 py-0.5 text-[10.5px] font-bold ring-1 ring-inset" :class="statusPill(focalRun.status)">{{ statusLabel(focalRun.status) }}</span></dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">操作人</dt><dd class="font-mono text-slate-700">{{ focalRun.operator }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">分区</dt><dd class="font-mono text-blue-600 font-semibold">{{ focalRun.partition }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">耗时</dt><dd class="font-mono text-slate-700">{{ focalRun.duration }}</dd></div>
          </dl>
        </div>

        <div class="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">已物化资产</p>
          <ul class="space-y-1 text-[11.5px]">
            <li v-for="ev in matEvents" :key="ev.ts" class="flex items-center gap-2">
              <span class="h-1.5 w-1.5 rounded-full bg-blue-500"></span>
              <span class="truncate font-mono text-slate-700">{{ ev.msg.split(' ')[0] }}</span>
              <span class="ml-auto font-mono text-[10.5px] text-slate-500">{{ ev.metadata?.num_rows ? ev.metadata.num_rows.toLocaleString() : '—' }}</span>
            </li>
            <li v-if="!matEvents.length" class="text-[11px] text-slate-400">没有成功物化</li>
          </ul>
        </div>

        <div class="flex min-h-0 flex-1 flex-col rounded-2xl border border-slate-200 bg-white shadow-sm">
          <p class="border-b border-slate-200 px-3 py-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">资产检查 ({{ checkEvents.length }})</p>
          <ul class="flex-1 overflow-auto">
            <li v-for="(ev, idx) in checkEvents" :key="idx" class="border-b border-slate-100 px-3 py-1.5 last:border-0 text-[11.5px]">
              <span class="text-emerald-600">✓</span>
              <span class="ml-1.5 font-mono text-slate-700">{{ ev.msg.split('：')[0] }}</span>
              <p class="ml-4 mt-0.5 font-mono text-[10.5px] text-slate-500">{{ ev.msg.split('：').slice(1).join('：').trim() }}</p>
            </li>
          </ul>
        </div>
      </aside>
    </div>
  </div>
</template>
