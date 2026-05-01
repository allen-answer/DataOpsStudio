<script setup>
import { computed, ref } from 'vue'
import { focalRun, runSteps, runEvents, assetHealth } from '../../mock/dagster'

const emit = defineEmits(['back'])

const eventFilter = ref('all')   // all / mat / log / error
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
  success: 'bg-emerald-500/80 ring-1 ring-emerald-400/40',
  failed:  'bg-rose-500/85 ring-1 ring-rose-400/50',
  running: 'bg-cyan-500/80 ring-1 ring-cyan-400/50 animate-pulse',
  skipped: 'bg-slate-700 ring-1 ring-slate-600',
}[status] || 'bg-slate-600')

const eventTypeMeta = {
  RUN_START:           { glyph: '▶', text: 'text-cyan-300',    label: 'RUN_START' },
  RUN_FAILURE:         { glyph: '✕', text: 'text-rose-300',    label: 'RUN_FAILURE' },
  STEP_START:          { glyph: '·', text: 'text-slate-400',   label: 'STEP_START' },
  STEP_SUCCESS:        { glyph: '✓', text: 'text-emerald-300', label: 'STEP_SUCCESS' },
  STEP_FAILURE:        { glyph: '✕', text: 'text-rose-300',    label: 'STEP_FAILURE' },
  MATERIALIZATION:     { glyph: '◈', text: 'text-cyan-300',    label: 'MATERIALIZATION' },
  EXPECTATION_RESULT:  { glyph: '✓', text: 'text-emerald-300', label: 'EXPECTATION_RESULT' },
  LOG:                 { glyph: '›', text: 'text-slate-400',   label: 'LOG' },
}

const levelClass = (level) => ({
  INFO:  'text-slate-400',
  WARN:  'text-amber-400',
  ERROR: 'text-rose-400',
}[level] || 'text-slate-400')

const matEvents  = computed(() => runEvents.filter((e) => e.type === 'MATERIALIZATION'))
const checkEvents = computed(() => runEvents.filter((e) => e.type === 'EXPECTATION_RESULT'))
const errorEvents = computed(() => runEvents.filter((e) => e.level === 'ERROR' || e.type === 'STEP_FAILURE'))

const statusPill = (status) => ({
  success: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  failed:  'bg-rose-500/10 text-rose-300 ring-rose-500/30',
  running: 'bg-cyan-500/10 text-cyan-300 ring-cyan-500/30',
}[status] || 'bg-slate-700 text-slate-400 ring-slate-700')
</script>

<template>
  <div class="flex h-[calc(100vh-160px)] flex-col gap-3 text-slate-100">
    <!-- header -->
    <div class="rounded border border-slate-800 bg-slate-900 px-4 py-3">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-[11px] text-slate-500">
            <button class="text-slate-400 transition hover:text-slate-200" @click="emit('back')">← Asset graph</button>
            <span class="text-slate-700">/</span>
            <span>Runs</span>
            <span class="text-slate-700">/</span>
            <span class="font-mono">{{ focalRun.id }}</span>
          </div>
          <div class="mt-1 flex items-center gap-2">
            <span class="rounded px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset" :class="statusPill(focalRun.status)">{{ focalRun.status.toUpperCase() }}</span>
            <span class="font-mono text-[14px]">{{ focalRun.triggered_by }}</span>
            <span class="text-slate-700">·</span>
            <span class="font-mono text-[12px] text-slate-400">partition {{ focalRun.partition }}</span>
            <span class="text-slate-700">·</span>
            <span class="font-mono text-[12px] text-slate-400">{{ focalRun.duration }}</span>
          </div>
          <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
            <span v-for="(value, key) in focalRun.tags" :key="key" class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10.5px]">
              <span class="text-slate-500">{{ key }}=</span><span class="text-slate-200">{{ value }}</span>
            </span>
            <span class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-300">{{ focalRun.code_location }}</span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button class="inline-flex h-8 items-center gap-1.5 rounded bg-amber-500/90 px-3 text-xs font-semibold text-amber-950 transition hover:bg-amber-400">
            ⟳ Re-execute failed
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-700 px-2.5 text-xs font-semibold text-slate-300 transition hover:border-slate-600">
            Re-execute all
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded border border-rose-500/40 bg-rose-500/10 px-2.5 text-xs font-semibold text-rose-300 transition hover:bg-rose-500/20">
            ▣ Terminate
          </button>
        </div>
      </div>

      <!-- run summary stats -->
      <div class="mt-3 grid grid-cols-2 gap-2 md:grid-cols-5">
        <div class="rounded border border-slate-800 bg-slate-950/40 px-3 py-2">
          <p class="font-mono text-[10px] uppercase tracking-wider text-slate-500">Started</p>
          <p class="mt-0.5 font-mono text-[12.5px] text-slate-200">{{ focalRun.started_at }}</p>
        </div>
        <div class="rounded border border-slate-800 bg-slate-950/40 px-3 py-2">
          <p class="font-mono text-[10px] uppercase tracking-wider text-slate-500">Ended</p>
          <p class="mt-0.5 font-mono text-[12.5px] text-slate-200">{{ focalRun.ended_at }}</p>
        </div>
        <div class="rounded border border-slate-800 bg-slate-950/40 px-3 py-2">
          <p class="font-mono text-[10px] uppercase tracking-wider text-slate-500">Steps</p>
          <p class="mt-0.5 font-mono text-[12.5px]"><span class="text-emerald-400">{{ focalRun.step_summary.success }}</span> / <span class="text-rose-400">{{ focalRun.step_summary.failed }}</span> / <span class="text-slate-400">{{ focalRun.step_summary.total }}</span></p>
        </div>
        <div class="rounded border border-slate-800 bg-slate-950/40 px-3 py-2">
          <p class="font-mono text-[10px] uppercase tracking-wider text-slate-500">Materializations</p>
          <p class="mt-0.5 font-mono text-[12.5px] text-cyan-300">{{ matEvents.length }}</p>
        </div>
        <div class="rounded border border-slate-800 bg-slate-950/40 px-3 py-2">
          <p class="font-mono text-[10px] uppercase tracking-wider text-slate-500">Errors</p>
          <p class="mt-0.5 font-mono text-[12.5px]" :class="errorEvents.length > 0 ? 'text-rose-300' : 'text-slate-300'">{{ errorEvents.length }}</p>
        </div>
      </div>
    </div>

    <!-- gantt-like step timeline -->
    <div class="rounded border border-slate-800 bg-slate-900">
      <div class="flex items-center justify-between border-b border-slate-800 px-3 py-2">
        <span class="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Steps · {{ totalDurationS }}s total</span>
        <button v-if="stepFilter" class="text-[10.5px] text-cyan-400 hover:text-cyan-300" @click="stepFilter = ''">Clear filter ✕</button>
      </div>
      <div class="space-y-1.5 p-3">
        <button
          v-for="step in runSteps" :key="step.name"
          class="grid w-full grid-cols-[160px_minmax(0,1fr)_64px] items-center gap-3 rounded px-2 py-1.5 text-left transition"
          :class="stepFilter === step.name ? 'bg-cyan-500/10 ring-1 ring-cyan-500/40' : 'hover:bg-slate-800/60'"
          @click="stepFilter = stepFilter === step.name ? '' : step.name"
        >
          <span class="truncate font-mono text-[11.5px]">
            <span class="text-slate-400">{{ step.asset }}</span>
          </span>
          <span class="relative h-3 rounded bg-slate-800/70">
            <span class="absolute top-0 h-3 rounded" :class="stepStatusClass(step.status)" :style="stepBarStyle(step)"></span>
          </span>
          <span class="text-right font-mono text-[10.5px] text-slate-500">{{ step.duration_s }}s</span>
        </button>
      </div>
    </div>

    <!-- event stream + side panel -->
    <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_320px] gap-3 overflow-hidden">
      <!-- event stream -->
      <div class="flex min-h-0 flex-col rounded border border-slate-800 bg-slate-950/60">
        <div class="flex items-center gap-2 border-b border-slate-800 bg-slate-900/80 px-3 py-1.5">
          <span class="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Event stream</span>
          <span class="text-slate-700">·</span>
          <span class="font-mono text-[10.5px] text-slate-500">{{ filteredEvents.length }} / {{ runEvents.length }}</span>
          <div class="ml-auto flex items-center gap-1">
            <button
              v-for="key in ['all','mat','check','log','error']" :key="key"
              class="rounded border px-2 py-0.5 text-[10.5px] capitalize transition"
              :class="eventFilter === key ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400 hover:text-slate-200'"
              @click="eventFilter = key"
            >{{ key === 'mat' ? 'mats' : (key === 'check' ? 'checks' : key) }}</button>
          </div>
        </div>
        <div class="min-h-0 flex-1 overflow-auto font-mono text-[12px]">
          <div v-if="!filteredEvents.length" class="px-4 py-8 text-center text-slate-500">No events match the current filters.</div>
          <div v-for="(ev, idx) in filteredEvents" :key="idx" class="grid grid-cols-[120px_120px_1fr] items-start gap-3 border-b border-slate-800/60 px-3 py-1.5 last:border-0 transition hover:bg-slate-900/60">
            <span class="font-mono text-[10.5px] text-slate-500">{{ ev.ts }}</span>
            <span class="flex items-center gap-1.5 truncate">
              <span class="grid h-4 w-4 shrink-0 place-items-center text-[11px]" :class="eventTypeMeta[ev.type]?.text || 'text-slate-400'">{{ eventTypeMeta[ev.type]?.glyph || '·' }}</span>
              <span class="truncate text-[10.5px] font-bold uppercase tracking-wider" :class="eventTypeMeta[ev.type]?.text || 'text-slate-400'">{{ ev.type }}</span>
            </span>
            <div class="min-w-0">
              <p class="break-words" :class="levelClass(ev.level)">{{ ev.msg }}</p>
              <p v-if="ev.step" class="mt-0.5 text-[10.5px] text-slate-500">step={{ ev.step }}</p>
              <div v-if="ev.metadata" class="mt-1 flex flex-wrap gap-1">
                <span v-for="(value, key) in ev.metadata" :key="key" class="rounded bg-slate-800 px-1.5 py-0.5 text-[10.5px]">
                  <span class="text-slate-500">{{ key }}=</span><span class="text-slate-200">{{ value }}</span>
                </span>
              </div>
              <pre v-if="ev.stack" class="mt-1.5 overflow-x-auto rounded border border-rose-500/20 bg-rose-500/5 p-2 text-[11px] leading-relaxed text-rose-200">{{ ev.stack.join('\n') }}</pre>
            </div>
          </div>
        </div>
      </div>

      <!-- side panel: run sidebar -->
      <aside class="flex min-h-0 flex-col gap-3 overflow-hidden">
        <div class="rounded border border-slate-800 bg-slate-900 p-3">
          <p class="mb-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Run summary</p>
          <dl class="space-y-1.5 text-[12px]">
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Run ID</dt><dd class="font-mono text-slate-200">{{ focalRun.id }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Status</dt><dd><span class="rounded px-1.5 py-0.5 text-[10.5px] font-bold ring-1 ring-inset" :class="statusPill(focalRun.status)">{{ focalRun.status }}</span></dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Operator</dt><dd class="font-mono">{{ focalRun.operator }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Partition</dt><dd class="font-mono text-cyan-300">{{ focalRun.partition }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Duration</dt><dd class="font-mono">{{ focalRun.duration }}</dd></div>
          </dl>
        </div>

        <div class="rounded border border-slate-800 bg-slate-900 p-3">
          <p class="mb-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Materialized assets</p>
          <ul class="space-y-1 text-[11.5px]">
            <li v-for="ev in matEvents" :key="ev.ts" class="flex items-center gap-2">
              <span class="h-1.5 w-1.5 rounded-full bg-cyan-400"></span>
              <span class="truncate font-mono text-slate-200">{{ ev.msg.split(' ')[0] }}</span>
              <span class="ml-auto font-mono text-[10.5px] text-slate-500">{{ ev.metadata?.num_rows ? ev.metadata.num_rows.toLocaleString() : '—' }}</span>
            </li>
            <li v-if="!matEvents.length" class="text-[11px] text-slate-500">No materializations</li>
          </ul>
        </div>

        <div class="flex min-h-0 flex-1 flex-col rounded border border-slate-800 bg-slate-900">
          <p class="border-b border-slate-800 px-3 py-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Asset checks ({{ checkEvents.length }})</p>
          <ul class="flex-1 overflow-auto">
            <li v-for="(ev, idx) in checkEvents" :key="idx" class="border-b border-slate-800/60 px-3 py-1.5 last:border-0 text-[11.5px]">
              <span class="text-emerald-400">✓</span>
              <span class="ml-1.5 font-mono text-slate-200">{{ ev.msg.split(':')[0] }}</span>
              <p class="ml-4 mt-0.5 font-mono text-[10.5px] text-slate-500">{{ ev.msg.split(':').slice(1).join(':').trim() }}</p>
            </li>
          </ul>
        </div>
      </aside>
    </div>
  </div>
</template>
