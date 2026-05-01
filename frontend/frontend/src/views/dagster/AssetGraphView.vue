<script setup>
import { computed, ref } from 'vue'
import { assets, assetEdges, assetGroups, assetKinds, assetHealth, sensorActivity } from '../../mock/dagster'

const emit = defineEmits(['open-asset', 'open-run'])

const NODE_W = 232
const NODE_H = 72

const groupFilter = ref('all')
const healthFilter = ref('all')
const searchTerm = ref('')

const filteredAssets = computed(() => assets.filter((asset) => {
  if (groupFilter.value !== 'all' && asset.group !== groupFilter.value) return false
  if (healthFilter.value !== 'all' && asset.health !== healthFilter.value) return false
  if (searchTerm.value && !asset.key.includes(searchTerm.value.toLowerCase())) return false
  return true
}))

const visibleKeys = computed(() => new Set(filteredAssets.value.map((a) => a.key)))

const visibleEdges = computed(() => assetEdges.filter((e) => visibleKeys.value.has(e.source) && visibleKeys.value.has(e.target)))

const assetByKey = computed(() => {
  const m = {}
  for (const a of assets) m[a.key] = a
  return m
})

const canvas = computed(() => {
  let maxX = 0, maxY = 0
  for (const asset of assets) {
    if (asset.x + NODE_W > maxX) maxX = asset.x + NODE_W
    if (asset.y + NODE_H > maxY) maxY = asset.y + NODE_H
  }
  return { width: maxX + 60, height: maxY + 60 }
})

const edgePath = (edge) => {
  const s = assetByKey.value[edge.source]
  const t = assetByKey.value[edge.target]
  if (!s || !t) return ''
  const sx = s.x + NODE_W
  const sy = s.y + NODE_H / 2
  const tx = t.x
  const ty = t.y + NODE_H / 2
  const dx = Math.max(40, (tx - sx) * 0.5)
  return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`
}

const selectedAssetKey = ref('analytics/daily_revenue')
const selectedAsset = computed(() => assetByKey.value[selectedAssetKey.value])

// Lane background bands derived from group y-extent.
const lanes = computed(() => {
  const out = []
  for (const group of assetGroups) {
    const inGroup = assets.filter((a) => a.group === group.id)
    if (!inGroup.length) continue
    const minX = Math.min(...inGroup.map((a) => a.x)) - 16
    const maxX = Math.max(...inGroup.map((a) => a.x)) + NODE_W + 16
    out.push({ id: group.id, name: group.name, color: group.color, x: minX, width: maxX - minX })
  }
  return out
})

const healthCounts = computed(() => {
  const c = { fresh: 0, stale: 0, failed: 0, materializing: 0, none: 0 }
  for (const asset of filteredAssets.value) c[asset.health] = (c[asset.health] || 0) + 1
  return c
})

const edgeHighlighted = (edge) => edge.source === selectedAssetKey.value || edge.target === selectedAssetKey.value
</script>

<template>
  <div class="flex h-[calc(100vh-160px)] flex-col gap-3 text-slate-100">
    <!-- summary strip -->
    <div class="grid grid-cols-2 gap-2 md:grid-cols-6">
      <div class="rounded border border-slate-800 bg-slate-900 px-3 py-2.5">
        <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Assets</p>
        <p class="mt-1 font-mono text-2xl font-bold tabular-nums">{{ filteredAssets.length }}</p>
        <p class="mt-0.5 text-[10px] text-slate-500">/ {{ assets.length }} total</p>
      </div>
      <div class="rounded border border-slate-800 bg-slate-900 px-3 py-2.5">
        <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Fresh</p>
        <p class="mt-1 font-mono text-2xl font-bold tabular-nums text-emerald-400">{{ healthCounts.fresh }}</p>
        <p class="mt-0.5 text-[10px] text-slate-500">last 24h</p>
      </div>
      <div class="rounded border border-slate-800 bg-slate-900 px-3 py-2.5">
        <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Stale</p>
        <p class="mt-1 font-mono text-2xl font-bold tabular-nums text-amber-400">{{ healthCounts.stale }}</p>
        <p class="mt-0.5 text-[10px] text-slate-500">freshness SLA missed</p>
      </div>
      <div class="rounded border border-slate-800 bg-slate-900 px-3 py-2.5">
        <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Failed</p>
        <p class="mt-1 font-mono text-2xl font-bold tabular-nums text-rose-400">{{ healthCounts.failed }}</p>
        <p class="mt-0.5 text-[10px] text-slate-500">last materialization</p>
      </div>
      <div class="rounded border border-slate-800 bg-slate-900 px-3 py-2.5">
        <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">In flight</p>
        <p class="mt-1 flex items-center gap-2 font-mono text-2xl font-bold tabular-nums text-cyan-400">
          <span class="h-2 w-2 rounded-full bg-cyan-400 animate-pulse"></span>{{ healthCounts.materializing }}
        </p>
        <p class="mt-0.5 text-[10px] text-slate-500">materializing now</p>
      </div>
      <div class="rounded border border-slate-800 bg-slate-900 px-3 py-2.5">
        <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Sensors / schedules</p>
        <p class="mt-1 font-mono text-2xl font-bold tabular-nums">{{ sensorActivity.active }}<span class="text-slate-600 text-sm"> / {{ sensorActivity.active + sensorActivity.paused }}</span></p>
        <p class="mt-0.5 text-[10px] text-slate-500">active</p>
      </div>
    </div>

    <!-- toolbar -->
    <div class="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-900 px-3 py-2">
      <div class="flex items-center gap-1.5 text-xs">
        <span class="text-[10px] font-semibold uppercase text-slate-500">Group</span>
        <button
          class="rounded border px-2 py-0.5 font-mono text-[11px] transition"
          :class="groupFilter === 'all' ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400 hover:text-slate-200'"
          @click="groupFilter = 'all'"
        >all</button>
        <button
          v-for="g in assetGroups" :key="g.id"
          class="rounded border px-2 py-0.5 font-mono text-[11px] transition"
          :class="groupFilter === g.id ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400 hover:text-slate-200'"
          @click="groupFilter = g.id"
        >{{ g.name }}</button>
      </div>
      <div class="ml-2 flex items-center gap-1.5 text-xs">
        <span class="text-[10px] font-semibold uppercase text-slate-500">Health</span>
        <button
          v-for="key in ['all','fresh','stale','failed','materializing','none']" :key="key"
          class="rounded border px-2 py-0.5 text-[11px] capitalize transition"
          :class="healthFilter === key ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400 hover:text-slate-200'"
          @click="healthFilter = key"
        >{{ key === 'all' ? 'all' : assetHealth[key].label.toLowerCase() }}</button>
      </div>
      <div class="ml-auto flex items-center gap-1.5">
        <input
          v-model="searchTerm"
          placeholder="Filter by asset key…"
          class="h-7 w-56 rounded border border-slate-700 bg-slate-950 px-2 font-mono text-[12px] text-slate-200 placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none"
        >
        <button class="inline-flex h-7 items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 text-[11px] font-semibold text-slate-300 transition hover:border-slate-600">
          <span class="text-slate-400">⟳</span> Reload
        </button>
        <button class="inline-flex h-7 items-center gap-1 rounded bg-emerald-500/90 px-2.5 text-[11px] font-semibold text-emerald-950 transition hover:bg-emerald-400">
          ▶ Materialize selected
        </button>
      </div>
    </div>

    <!-- main: graph + side panel -->
    <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_320px] gap-3">
      <!-- graph canvas -->
      <div class="relative flex min-h-0 flex-col overflow-hidden rounded border border-slate-800 bg-slate-950/60">
        <div class="flex items-center justify-between border-b border-slate-800 bg-slate-900/80 px-3 py-1.5 backdrop-blur">
          <div class="flex items-center gap-3 text-[11px]">
            <span class="font-semibold text-slate-400">Asset graph</span>
            <span class="text-slate-700">·</span>
            <span class="font-mono text-slate-500">{{ visibleEdges.length }} edges · {{ filteredAssets.length }} nodes</span>
          </div>
          <div class="flex items-center gap-1 text-[11px]">
            <button class="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-slate-400 hover:text-slate-200">100%</button>
            <button class="rounded border border-slate-700 bg-slate-900 p-1 text-slate-400 hover:text-slate-200" title="Center"><svg class="h-3 w-3" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="14" height="14" rx="1"/><circle cx="10" cy="10" r="2"/></svg></button>
          </div>
        </div>
        <div class="relative flex-1 overflow-auto">
          <div
            class="relative"
            :style="{
              width: canvas.width + 'px', height: canvas.height + 'px',
              backgroundImage: 'radial-gradient(circle at 1px 1px, rgb(51 65 85 / 0.5) 1px, transparent 0)',
              backgroundSize: '24px 24px',
            }"
          >
            <!-- group lanes (faint tinted bands) -->
            <div
              v-for="lane in lanes" :key="lane.id"
              class="absolute top-2 bottom-2 rounded border border-dashed border-slate-800/80"
              :style="{ left: lane.x + 'px', width: lane.width + 'px' }"
            >
              <span class="ml-2 mt-1 inline-block rounded bg-slate-900/80 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-500" :style="{ borderLeft: `2px solid ${lane.color}` }">{{ lane.name }}</span>
            </div>

            <!-- edges -->
            <svg class="pointer-events-none absolute inset-0" :width="canvas.width" :height="canvas.height" :viewBox="`0 0 ${canvas.width} ${canvas.height}`">
              <defs>
                <marker id="dg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill="#475569"/>
                </marker>
                <marker id="dg-arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill="#22d3ee"/>
                </marker>
              </defs>
              <path
                v-for="(edge, idx) in visibleEdges" :key="idx"
                :d="edgePath(edge)" fill="none"
                :stroke="edgeHighlighted(edge) ? '#22d3ee' : '#475569'"
                :stroke-width="edgeHighlighted(edge) ? 2 : 1.2"
                :stroke-opacity="edgeHighlighted(edge) ? 0.9 : 0.55"
                :marker-end="edgeHighlighted(edge) ? 'url(#dg-arrow-active)' : 'url(#dg-arrow)'"
              />
            </svg>

            <!-- asset cards -->
            <button
              v-for="asset in filteredAssets" :key="asset.key"
              class="absolute flex flex-col gap-1 rounded border bg-slate-900/95 px-2.5 py-2 text-left backdrop-blur transition hover:border-slate-600"
              :class="selectedAssetKey === asset.key ? 'border-cyan-500/70 ring-1 ring-cyan-500/50' : 'border-slate-700'"
              :style="{ left: asset.x + 'px', top: asset.y + 'px', width: NODE_W + 'px', height: NODE_H + 'px' }"
              @click="selectedAssetKey = asset.key"
              @dblclick="emit('open-asset', asset.key)"
            >
              <div class="flex items-center gap-1.5">
                <span class="h-2 w-2 shrink-0 rounded-full" :class="assetHealth[asset.health].dot"></span>
                <span class="truncate font-mono text-[12.5px] font-semibold text-slate-100">{{ asset.key }}</span>
              </div>
              <div class="flex items-center gap-1.5 text-[10.5px]">
                <span
                  class="rounded px-1 font-mono text-[9.5px] font-bold"
                  :style="{ background: assetKinds[asset.kind].accent + '24', color: assetKinds[asset.kind].accent }"
                >{{ assetKinds[asset.kind].glyph }} {{ assetKinds[asset.kind].label }}</span>
                <span v-if="asset.health !== 'none'" class="font-mono text-slate-500 truncate">{{ asset.last_materialized.slice(5, 16) || '—' }}</span>
                <span v-else class="font-mono text-slate-600">never run</span>
                <span class="ml-auto font-mono text-slate-500">{{ asset.duration }}</span>
              </div>
            </button>
          </div>
        </div>

        <!-- legend bar -->
        <div class="flex items-center gap-3 border-t border-slate-800 bg-slate-900/80 px-3 py-1.5 text-[10.5px] text-slate-500">
          <span class="font-semibold uppercase tracking-wider">Health</span>
          <span v-for="(meta, key) in assetHealth" :key="key" class="flex items-center gap-1.5">
            <span class="h-1.5 w-1.5 rounded-full" :class="meta.dot"></span>{{ meta.label }}
          </span>
          <span class="ml-auto font-mono text-slate-600">⌘+click to focus · 双击 open detail</span>
        </div>
      </div>

      <!-- side panel: selected asset + sensor feed -->
      <aside class="flex min-h-0 flex-col gap-3 overflow-hidden">
        <div class="rounded border border-slate-800 bg-slate-900">
          <div class="flex items-center justify-between border-b border-slate-800 px-3 py-2">
            <span class="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Selected asset</span>
            <button class="text-[10.5px] text-cyan-400 hover:text-cyan-300" @click="selectedAsset && emit('open-asset', selectedAsset.key)">Open detail →</button>
          </div>
          <div v-if="selectedAsset" class="space-y-3 p-3 text-[12px]">
            <div>
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full" :class="assetHealth[selectedAsset.health].dot"></span>
                <span class="truncate font-mono text-[13.5px] font-semibold">{{ selectedAsset.key }}</span>
              </div>
              <div class="mt-1.5 flex items-center gap-1.5">
                <span class="rounded px-1.5 py-0.5 text-[10.5px] font-semibold ring-1 ring-inset" :class="assetHealth[selectedAsset.health].pill">
                  {{ assetHealth[selectedAsset.health].label }}
                </span>
                <span class="rounded px-1.5 py-0.5 font-mono text-[10.5px] font-bold" :style="{ background: assetKinds[selectedAsset.kind].accent + '24', color: assetKinds[selectedAsset.kind].accent }">
                  {{ assetKinds[selectedAsset.kind].label }}
                </span>
                <span class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-300">{{ selectedAsset.group }}</span>
              </div>
            </div>
            <dl class="space-y-1.5 text-[11.5px]">
              <div class="flex justify-between gap-2"><dt class="text-slate-500">Owner</dt><dd class="font-mono">{{ selectedAsset.owner }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">Last materialized</dt><dd class="font-mono text-slate-300">{{ selectedAsset.last_materialized || '—' }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">Run id</dt><dd class="font-mono text-slate-400">{{ selectedAsset.last_run_id ? selectedAsset.last_run_id.slice(0, 13) : '—' }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">Rows</dt><dd class="font-mono">{{ selectedAsset.rows ? selectedAsset.rows.toLocaleString() : '—' }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">Duration</dt><dd class="font-mono">{{ selectedAsset.duration }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">Upstream</dt><dd class="font-mono">{{ selectedAsset.upstream.length }}</dd></div>
            </dl>
            <div v-if="selectedAsset.partitions" class="border-t border-slate-800 pt-2.5">
              <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Partitions (last 30d)</p>
              <div class="flex items-center gap-2 text-[11px]">
                <span class="text-emerald-400 font-mono">●{{ selectedAsset.partitions.fresh }}</span>
                <span class="text-amber-400 font-mono">●{{ selectedAsset.partitions.stale }}</span>
                <span class="text-rose-400 font-mono">●{{ selectedAsset.partitions.failed }}</span>
              </div>
            </div>
            <div class="flex flex-wrap gap-1.5 border-t border-slate-800 pt-2.5">
              <button class="inline-flex items-center gap-1 rounded bg-emerald-500/90 px-2 py-1 text-[11px] font-semibold text-emerald-950 hover:bg-emerald-400">▶ Materialize</button>
              <button class="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:border-slate-600">Backfill…</button>
              <button class="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:border-slate-600" @click="emit('open-run', selectedAsset.last_run_id)">View run</button>
            </div>
          </div>
        </div>

        <div class="flex min-h-0 flex-1 flex-col rounded border border-slate-800 bg-slate-900">
          <div class="border-b border-slate-800 px-3 py-2 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Sensors / schedules</div>
          <ul class="flex-1 overflow-auto">
            <li v-for="(s, idx) in sensorActivity.recent_ticks" :key="idx" class="flex items-center gap-2 border-b border-slate-800/70 px-3 py-2 last:border-0 text-[11.5px]">
              <span class="font-mono text-[10px] uppercase text-slate-500">{{ s.type }}</span>
              <span class="truncate font-mono text-slate-200">{{ s.name }}</span>
              <span class="ml-auto font-mono text-slate-500">{{ s.last_tick }}</span>
              <span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider"
                    :class="s.status === 'fired' ? 'bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500/30' : 'bg-slate-800 text-slate-400 ring-1 ring-slate-700'"
              >{{ s.status }}</span>
            </li>
          </ul>
        </div>
      </aside>
    </div>
  </div>
</template>
