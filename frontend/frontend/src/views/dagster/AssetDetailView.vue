<script setup>
import { computed, ref } from 'vue'
import {
  assets, assetEdges,
  assetKinds, assetHealth,
  focalAssetKey, materializations, assetChecks, partitionStrip, focalDefinition,
} from '../../mock/dagster'

const emit = defineEmits(['back', 'open-run'])

const assetByKey = computed(() => {
  const m = {}
  for (const a of assets) m[a.key] = a
  return m
})
const focal = computed(() => assetByKey.value[focalAssetKey])

const tab = ref('overview')   // overview / mats / checks / lineage / definition

const upstreamAssets = computed(() => focal.value.upstream.map((k) => assetByKey.value[k]).filter(Boolean))
const downstreamAssets = computed(() => assetEdges.filter((e) => e.source === focal.value.key).map((e) => assetByKey.value[e.target]).filter(Boolean))

const matStatusMeta = {
  success: { dot: 'bg-emerald-500', text: 'text-emerald-400', pill: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
  failed:  { dot: 'bg-rose-500',    text: 'text-rose-400',    pill: 'bg-rose-500/10 text-rose-300 ring-rose-500/30' },
}

const partitionDot = (status) => {
  if (status === 'ok') return 'bg-emerald-500/80'
  if (status === 'stale') return 'bg-amber-500/80'
  if (status === 'failed') return 'bg-rose-500/90'
  return 'bg-slate-700'
}
const partitionLabel = (status) => ({ ok: 'OK', stale: 'STALE', failed: 'FAILED', missing: 'MISSING' }[status] || status)

const checkSeverityClass = (sev) => sev === 'ERROR' ? 'text-rose-300 bg-rose-500/10 ring-rose-500/30' : 'text-amber-300 bg-amber-500/10 ring-amber-500/30'

const tabs = [
  { id: 'overview',   label: 'Overview' },
  { id: 'mats',       label: 'Materializations', count: materializations.length },
  { id: 'checks',     label: 'Checks',           count: assetChecks.length },
  { id: 'lineage',    label: 'Lineage' },
  { id: 'definition', label: 'Definition' },
]

const codeLines = computed(() => focalDefinition.split('\n'))
</script>

<template>
  <div class="flex h-[calc(100vh-160px)] flex-col gap-3 text-slate-100">
    <!-- header -->
    <div class="rounded border border-slate-800 bg-slate-900">
      <div class="flex items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-[11px] text-slate-500">
            <button class="text-slate-400 transition hover:text-slate-200" @click="emit('back')">← Asset graph</button>
            <span class="text-slate-700">/</span>
            <span class="font-mono">{{ focal.group }}</span>
          </div>
          <div class="mt-1 flex items-center gap-2.5">
            <span class="h-2.5 w-2.5 rounded-full" :class="assetHealth[focal.health].dot"></span>
            <h1 class="font-mono text-xl font-semibold">{{ focal.key }}</h1>
            <span class="rounded px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="assetHealth[focal.health].pill">
              {{ assetHealth[focal.health].label }}
            </span>
            <span class="rounded px-1.5 py-0.5 font-mono text-[11px] font-bold" :style="{ background: assetKinds[focal.kind].accent + '24', color: assetKinds[focal.kind].accent }">
              {{ assetKinds[focal.kind].label }}
            </span>
          </div>
          <p class="mt-1.5 max-w-2xl text-[12px] text-slate-400">Daily revenue rollup for finance / exec dashboards.  Joins cleaned orders against currency rates and emits one row per (date, currency, business_unit).</p>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button class="inline-flex h-8 items-center gap-1.5 rounded bg-emerald-500/90 px-3 text-xs font-semibold text-emerald-950 transition hover:bg-emerald-400">
            <span>▶</span> Materialize
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-700 bg-slate-900 px-2.5 text-xs font-semibold text-slate-300 transition hover:border-slate-600">
            Backfill…
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-700 bg-slate-900 px-2.5 text-xs font-semibold text-slate-300 transition hover:border-slate-600" @click="emit('open-run', focal.last_run_id)">
            Latest run →
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded border border-slate-700 bg-slate-900 px-2.5 text-xs font-semibold text-slate-300 transition hover:border-slate-600">⋯</button>
        </div>
      </div>

      <!-- tabs -->
      <nav class="flex border-b border-slate-800 px-2 text-[12px]">
        <button
          v-for="t in tabs" :key="t.id"
          class="relative flex items-center gap-1.5 border-b-2 px-3 py-2 font-semibold transition"
          :class="tab === t.id ? 'border-cyan-400 text-cyan-300' : 'border-transparent text-slate-400 hover:text-slate-200'"
          @click="tab = t.id"
        >
          {{ t.label }}
          <span v-if="t.count !== undefined" class="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-mono text-slate-400">{{ t.count }}</span>
        </button>
      </nav>
    </div>

    <!-- tab content -->
    <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_320px] gap-3 overflow-hidden">
      <!-- main -->
      <div class="min-h-0 overflow-auto">
        <!-- OVERVIEW -->
        <div v-if="tab === 'overview'" class="space-y-3">
          <!-- partition heatmap -->
          <div class="rounded border border-slate-800 bg-slate-900">
            <div class="flex items-center justify-between border-b border-slate-800 px-3 py-2">
              <span class="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Partitions · last 30 days</span>
              <span class="font-mono text-[10.5px] text-slate-500">latest = {{ partitionStrip[partitionStrip.length - 1].partition }}</span>
            </div>
            <div class="p-3">
              <div class="grid grid-cols-30 gap-1" style="grid-template-columns: repeat(30, minmax(0, 1fr));">
                <div
                  v-for="cell in partitionStrip" :key="cell.partition"
                  class="aspect-square rounded-sm transition hover:scale-110"
                  :class="partitionDot(cell.status)"
                  :title="`${cell.partition} — ${partitionLabel(cell.status)}`"
                ></div>
              </div>
              <div class="mt-3 flex items-center gap-3 text-[10.5px] text-slate-500">
                <span class="flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-emerald-500/80"></span>OK</span>
                <span class="flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-amber-500/80"></span>STALE</span>
                <span class="flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-rose-500/90"></span>FAILED</span>
                <span class="flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-slate-700"></span>MISSING</span>
              </div>
            </div>
          </div>

          <!-- materialization timeline (compact) -->
          <div class="rounded border border-slate-800 bg-slate-900">
            <div class="flex items-center justify-between border-b border-slate-800 px-3 py-2">
              <span class="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Recent materializations</span>
              <button class="text-[10.5px] text-cyan-400 hover:text-cyan-300" @click="tab = 'mats'">View all →</button>
            </div>
            <ul class="divide-y divide-slate-800">
              <li v-for="mat in materializations.slice(0, 4)" :key="mat.id" class="flex items-center gap-3 px-3 py-2 text-[12px]">
                <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="matStatusMeta[mat.status].dot"></span>
                <span class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset" :class="matStatusMeta[mat.status].pill">{{ mat.status }}</span>
                <span class="shrink-0 font-mono text-slate-300">{{ mat.started_at.slice(5) }}</span>
                <span class="shrink-0 font-mono text-slate-500">partition={{ mat.partition }}</span>
                <span class="shrink-0 font-mono text-slate-500">{{ mat.duration }}</span>
                <button class="ml-auto truncate font-mono text-cyan-400 hover:text-cyan-300" @click="emit('open-run', mat.run_id)">{{ mat.run_id.slice(0, 13) }} →</button>
              </li>
            </ul>
          </div>

          <!-- checks summary -->
          <div class="rounded border border-slate-800 bg-slate-900">
            <div class="flex items-center justify-between border-b border-slate-800 px-3 py-2">
              <span class="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Asset checks</span>
              <button class="text-[10.5px] text-cyan-400 hover:text-cyan-300" @click="tab = 'checks'">View all →</button>
            </div>
            <ul class="divide-y divide-slate-800">
              <li v-for="check in assetChecks" :key="check.name" class="flex items-center gap-3 px-3 py-2 text-[12px]">
                <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="check.status === 'passed' ? 'bg-emerald-500' : 'bg-rose-500'"></span>
                <span class="shrink-0 font-mono font-semibold text-slate-200">{{ check.name }}</span>
                <span class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="checkSeverityClass(check.severity)">{{ check.severity }}</span>
                <span class="truncate text-slate-400">{{ check.message }}</span>
                <span class="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="check.status === 'passed' ? 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' : 'bg-rose-500/10 text-rose-300 ring-rose-500/30'">{{ check.status }}</span>
              </li>
            </ul>
          </div>
        </div>

        <!-- MATERIALIZATIONS -->
        <div v-else-if="tab === 'mats'" class="rounded border border-slate-800 bg-slate-900">
          <table class="w-full text-[12px]">
            <thead class="border-b border-slate-800 bg-slate-950/50">
              <tr class="text-left">
                <th class="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Status</th>
                <th class="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Started</th>
                <th class="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Partition</th>
                <th class="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Duration</th>
                <th class="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Operator</th>
                <th class="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Metadata</th>
                <th class="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Run</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="mat in materializations" :key="mat.id" class="border-b border-slate-800 last:border-0">
                <td class="px-3 py-2.5"><span class="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="matStatusMeta[mat.status].pill"><span class="h-1.5 w-1.5 rounded-full" :class="matStatusMeta[mat.status].dot"></span>{{ mat.status }}</span></td>
                <td class="px-3 py-2.5 font-mono text-slate-300">{{ mat.started_at }}</td>
                <td class="px-3 py-2.5 font-mono text-slate-400">{{ mat.partition }}</td>
                <td class="px-3 py-2.5 font-mono text-slate-400">{{ mat.duration }}</td>
                <td class="px-3 py-2.5 font-mono text-slate-400">{{ mat.operator }}</td>
                <td class="px-3 py-2.5">
                  <div class="flex flex-wrap gap-1">
                    <span v-for="(value, key) in mat.metadata" :key="key" class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10.5px]">
                      <span class="text-slate-500">{{ key }}=</span><span class="text-slate-200">{{ value }}</span>
                    </span>
                  </div>
                </td>
                <td class="px-3 py-2.5">
                  <button class="font-mono text-cyan-400 hover:text-cyan-300" @click="emit('open-run', mat.run_id)">{{ mat.run_id.slice(0, 13) }} →</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- CHECKS -->
        <div v-else-if="tab === 'checks'" class="space-y-2">
          <div v-for="check in assetChecks" :key="check.name" class="rounded border border-slate-800 bg-slate-900 p-3">
            <div class="flex items-center gap-2.5">
              <span class="h-2 w-2 rounded-full" :class="check.status === 'passed' ? 'bg-emerald-500' : 'bg-rose-500'"></span>
              <span class="font-mono text-[13.5px] font-semibold">{{ check.name }}</span>
              <span class="rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="checkSeverityClass(check.severity)">{{ check.severity }}</span>
              <span class="ml-auto rounded px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset" :class="check.status === 'passed' ? 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' : 'bg-rose-500/10 text-rose-300 ring-rose-500/30'">{{ check.status }}</span>
            </div>
            <p class="mt-1.5 text-[12px] text-slate-300">{{ check.message }}</p>
            <p class="mt-1 font-mono text-[10.5px] text-slate-500">last evaluated: {{ check.last_run }}</p>
          </div>
        </div>

        <!-- LINEAGE — focal +1-hop -->
        <div v-else-if="tab === 'lineage'" class="rounded border border-slate-800 bg-slate-900 p-4">
          <div class="grid grid-cols-3 gap-4">
            <div>
              <p class="mb-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Upstream ({{ upstreamAssets.length }})</p>
              <div class="space-y-1.5">
                <div v-for="up in upstreamAssets" :key="up.key" class="flex items-center gap-2 rounded border border-slate-800 bg-slate-950/50 px-2.5 py-2">
                  <span class="h-1.5 w-1.5 rounded-full" :class="assetHealth[up.health].dot"></span>
                  <span class="truncate font-mono text-[12px]">{{ up.key }}</span>
                  <span class="ml-auto rounded px-1 font-mono text-[9.5px] font-bold" :style="{ background: assetKinds[up.kind].accent + '24', color: assetKinds[up.kind].accent }">{{ assetKinds[up.kind].glyph }}</span>
                </div>
              </div>
            </div>
            <div class="flex flex-col items-center justify-center">
              <div class="text-center">
                <p class="text-[10.5px] uppercase tracking-wider text-slate-500">Focal</p>
                <div class="mt-2 rounded border-2 border-cyan-500/60 bg-cyan-500/5 px-3 py-3">
                  <div class="flex items-center gap-2">
                    <span class="h-2 w-2 rounded-full" :class="assetHealth[focal.health].dot"></span>
                    <span class="font-mono text-[13px] font-semibold">{{ focal.key }}</span>
                  </div>
                </div>
              </div>
            </div>
            <div>
              <p class="mb-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Downstream ({{ downstreamAssets.length }})</p>
              <div class="space-y-1.5">
                <div v-for="down in downstreamAssets" :key="down.key" class="flex items-center gap-2 rounded border border-slate-800 bg-slate-950/50 px-2.5 py-2">
                  <span class="h-1.5 w-1.5 rounded-full" :class="assetHealth[down.health].dot"></span>
                  <span class="truncate font-mono text-[12px]">{{ down.key }}</span>
                  <span class="ml-auto rounded px-1 font-mono text-[9.5px] font-bold" :style="{ background: assetKinds[down.kind].accent + '24', color: assetKinds[down.kind].accent }">{{ assetKinds[down.kind].glyph }}</span>
                </div>
                <p v-if="!downstreamAssets.length" class="rounded border border-dashed border-slate-800 px-3 py-2 text-center text-[11px] text-slate-500">No direct downstream</p>
              </div>
            </div>
          </div>
        </div>

        <!-- DEFINITION -->
        <div v-else-if="tab === 'definition'" class="rounded border border-slate-800 bg-slate-900">
          <div class="flex items-center justify-between border-b border-slate-800 px-3 py-2">
            <span class="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">analytics_repo / assets / daily_revenue.py</span>
            <button class="text-[10.5px] text-cyan-400 hover:text-cyan-300">Open in repo →</button>
          </div>
          <pre class="overflow-x-auto px-3 py-3 font-mono text-[12px] leading-relaxed"><span v-for="(line, idx) in codeLines" :key="idx" class="flex"><span class="mr-3 w-8 shrink-0 select-none text-right text-slate-700">{{ idx + 1 }}</span><span class="flex-1 whitespace-pre text-slate-300">{{ line }}</span><br/></span></pre>
        </div>
      </div>

      <!-- side panel: metadata always visible -->
      <aside class="flex min-h-0 flex-col overflow-hidden">
        <div class="rounded border border-slate-800 bg-slate-900 p-3">
          <p class="mb-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Properties</p>
          <dl class="space-y-1.5 text-[12px]">
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Group</dt><dd class="font-mono">{{ focal.group }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Kind</dt><dd class="font-mono">{{ assetKinds[focal.kind].label }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Owner</dt><dd class="font-mono">{{ focal.owner }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Last materialized</dt><dd class="font-mono text-slate-300">{{ focal.last_materialized }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Duration</dt><dd class="font-mono">{{ focal.duration }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">Rows</dt><dd class="font-mono">{{ focal.rows ? focal.rows.toLocaleString() : '—' }}</dd></div>
          </dl>
          <div class="mt-3 border-t border-slate-800 pt-2.5">
            <p class="mb-1.5 text-[10.5px] uppercase tracking-wider text-slate-500">Tags</p>
            <div class="flex flex-wrap gap-1">
              <span class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-300">tier=tier-1</span>
              <span class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-300">env=prod</span>
              <span class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-300">team=analytics</span>
            </div>
          </div>
        </div>

        <div class="mt-3 rounded border border-slate-800 bg-slate-900 p-3">
          <p class="mb-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Freshness policy</p>
          <p class="text-[12px] text-slate-300">Should be materialized within <span class="font-mono text-cyan-300">2 hours</span> of upstream changes.</p>
          <p class="mt-1.5 text-[11px] text-amber-400">⚠ Currently 6h12m past SLA</p>
        </div>

        <div class="mt-3 rounded border border-slate-800 bg-slate-900 p-3">
          <p class="mb-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Auto-materialization</p>
          <p class="text-[12px]">Policy: <span class="font-mono text-cyan-300">eager</span></p>
          <p class="mt-1 text-[11px] text-slate-500">Materializes when any parent has new data.</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.grid-cols-30 { grid-template-columns: repeat(30, minmax(0, 1fr)); }
</style>
