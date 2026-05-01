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
  <div class="flex h-[calc(100vh-200px)] flex-col gap-3">
    <!-- summary strip -->
    <div class="grid grid-cols-2 gap-3 md:grid-cols-6">
      <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">资产总数</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-slate-800">{{ filteredAssets.length }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">/ 共 {{ assets.length }} 个</p>
      </div>
      <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">最新</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-emerald-600">{{ healthCounts.fresh }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">近 24 小时</p>
      </div>
      <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">过期</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-amber-600">{{ healthCounts.stale }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">未达新鲜度 SLA</p>
      </div>
      <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">失败</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-rose-600">{{ healthCounts.failed }}</p>
        <p class="mt-0.5 text-[11px] text-slate-500">最近一次物化失败</p>
      </div>
      <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">物化中</p>
        <p class="mt-1 flex items-center gap-2 text-2xl font-bold tabular-nums text-blue-600">
          <span class="h-2 w-2 rounded-full bg-blue-500 animate-pulse"></span>{{ healthCounts.materializing }}
        </p>
        <p class="mt-0.5 text-[11px] text-slate-500">实时</p>
      </div>
      <div class="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">监听器 / 调度</p>
        <p class="mt-1 text-2xl font-bold tabular-nums text-slate-800">{{ sensorActivity.active }}<span class="text-slate-400 text-sm"> / {{ sensorActivity.active + sensorActivity.paused }}</span></p>
        <p class="mt-0.5 text-[11px] text-slate-500">已启用</p>
      </div>
    </div>

    <!-- toolbar -->
    <div class="flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
      <div class="flex items-center gap-1.5 text-xs">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">分组</span>
        <button
          class="rounded-lg border px-2 py-0.5 text-[11px] transition"
          :class="groupFilter === 'all' ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700'"
          @click="groupFilter = 'all'"
        >全部</button>
        <button
          v-for="g in assetGroups" :key="g.id"
          class="rounded-lg border px-2 py-0.5 text-[11px] transition"
          :class="groupFilter === g.id ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700'"
          @click="groupFilter = g.id"
        >{{ g.name }}</button>
      </div>
      <div class="ml-2 flex items-center gap-1.5 text-xs">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">状态</span>
        <button
          v-for="key in ['all','fresh','stale','failed','materializing','none']" :key="key"
          class="rounded-lg border px-2 py-0.5 text-[11px] transition"
          :class="healthFilter === key ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700'"
          @click="healthFilter = key"
        >{{ key === 'all' ? '全部' : assetHealth[key].label }}</button>
      </div>
      <div class="ml-auto flex items-center gap-1.5">
        <input
          v-model="searchTerm"
          placeholder="按资产 key 过滤..."
          class="h-7 w-56 rounded-lg border border-slate-200 bg-white px-2 text-[12px] text-slate-700 placeholder:text-slate-400 focus:border-blue-400 focus:outline-none"
        >
        <button class="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 text-[11px] font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">
          <span>↻</span> 刷新
        </button>
        <button class="inline-flex h-7 items-center gap-1.5 rounded-lg bg-blue-600 px-2.5 text-[11px] font-semibold text-white transition hover:bg-blue-700">
          ▶ 物化所选资产
        </button>
      </div>
    </div>

    <!-- main: graph + side panel -->
    <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_320px] gap-3">
      <!-- graph canvas -->
      <div class="relative flex min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div class="flex items-center justify-between border-b border-slate-200 bg-slate-50/60 px-3 py-1.5">
          <div class="flex items-center gap-3 text-[11px]">
            <span class="font-semibold text-slate-600">资产图谱</span>
            <span class="text-slate-300">·</span>
            <span class="text-slate-500">{{ visibleEdges.length }} 条依赖 · {{ filteredAssets.length }} 个节点</span>
          </div>
          <div class="flex items-center gap-1 text-[11px]">
            <button class="rounded-lg border border-slate-200 bg-white px-1.5 py-0.5 text-slate-500 hover:text-slate-700">100%</button>
            <button class="rounded-lg border border-slate-200 bg-white p-1 text-slate-500 hover:text-slate-700" title="居中"><svg class="h-3 w-3" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="14" height="14" rx="1"/><circle cx="10" cy="10" r="2"/></svg></button>
          </div>
        </div>
        <div class="relative flex-1 overflow-auto">
          <div
            class="relative"
            :style="{
              width: canvas.width + 'px', height: canvas.height + 'px',
              backgroundImage: 'radial-gradient(circle at 1px 1px, rgb(203 213 225 / 0.6) 1px, transparent 0)',
              backgroundSize: '24px 24px',
            }"
          >
            <!-- group lanes -->
            <div
              v-for="lane in lanes" :key="lane.id"
              class="absolute top-2 bottom-2 rounded-xl border border-dashed border-slate-200"
              :style="{ left: lane.x + 'px', width: lane.width + 'px' }"
            >
              <span class="ml-2 mt-1 inline-block rounded bg-white/80 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500" :style="{ borderLeft: `2px solid ${lane.color}` }">{{ lane.name }}</span>
            </div>

            <!-- edges -->
            <svg class="pointer-events-none absolute inset-0" :width="canvas.width" :height="canvas.height" :viewBox="`0 0 ${canvas.width} ${canvas.height}`">
              <defs>
                <marker id="dg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/>
                </marker>
                <marker id="dg-arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill="#2563eb"/>
                </marker>
              </defs>
              <path
                v-for="(edge, idx) in visibleEdges" :key="idx"
                :d="edgePath(edge)" fill="none"
                :stroke="edgeHighlighted(edge) ? '#2563eb' : '#94a3b8'"
                :stroke-width="edgeHighlighted(edge) ? 2 : 1.2"
                :stroke-opacity="edgeHighlighted(edge) ? 0.9 : 0.55"
                :marker-end="edgeHighlighted(edge) ? 'url(#dg-arrow-active)' : 'url(#dg-arrow)'"
              />
            </svg>

            <!-- asset cards -->
            <button
              v-for="asset in filteredAssets" :key="asset.key"
              class="absolute flex flex-col gap-1 rounded-xl border bg-white px-2.5 py-2 text-left shadow-sm transition hover:shadow-md"
              :class="selectedAssetKey === asset.key ? 'border-blue-400 ring-2 ring-blue-200' : 'border-slate-200'"
              :style="{ left: asset.x + 'px', top: asset.y + 'px', width: NODE_W + 'px', height: NODE_H + 'px' }"
              @click="selectedAssetKey = asset.key"
              @dblclick="emit('open-asset', asset.key)"
            >
              <div class="flex items-center gap-1.5">
                <span class="h-2 w-2 shrink-0 rounded-full" :class="assetHealth[asset.health].dot"></span>
                <span class="truncate font-mono text-[12.5px] font-semibold text-slate-800">{{ asset.key }}</span>
              </div>
              <div class="flex items-center gap-1.5 text-[10.5px]">
                <span
                  class="rounded px-1 font-mono text-[9.5px] font-bold"
                  :style="{ background: assetKinds[asset.kind].accent + '1A', color: assetKinds[asset.kind].accent }"
                >{{ assetKinds[asset.kind].glyph }} {{ assetKinds[asset.kind].label }}</span>
                <span v-if="asset.health !== 'none'" class="font-mono text-slate-500 truncate">{{ asset.last_materialized.slice(5, 16) || '—' }}</span>
                <span v-else class="font-mono text-slate-400">未运行</span>
                <span class="ml-auto font-mono text-slate-500">{{ asset.duration }}</span>
              </div>
            </button>
          </div>
        </div>

        <!-- legend bar -->
        <div class="flex items-center gap-3 border-t border-slate-200 bg-slate-50/60 px-3 py-1.5 text-[10.5px] text-slate-500">
          <span class="font-semibold uppercase tracking-wider">健康度</span>
          <span v-for="(meta, key) in assetHealth" :key="key" class="flex items-center gap-1.5">
            <span class="h-1.5 w-1.5 rounded-full" :class="meta.dot"></span>{{ meta.label }}
          </span>
          <span class="ml-auto text-slate-400">单击聚焦 · 双击进入资产详情</span>
        </div>
      </div>

      <!-- side panel: selected asset + sensor feed -->
      <aside class="flex min-h-0 flex-col gap-3 overflow-hidden">
        <div class="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
            <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">所选资产</span>
            <button class="text-[11px] font-semibold text-blue-600 hover:text-blue-700" @click="selectedAsset && emit('open-asset', selectedAsset.key)">查看详情 →</button>
          </div>
          <div v-if="selectedAsset" class="space-y-3 p-3 text-[12px]">
            <div>
              <div class="flex items-center gap-2">
                <span class="h-2 w-2 rounded-full" :class="assetHealth[selectedAsset.health].dot"></span>
                <span class="truncate font-mono text-[13.5px] font-semibold text-slate-800">{{ selectedAsset.key }}</span>
              </div>
              <div class="mt-1.5 flex items-center gap-1.5">
                <span class="rounded-full px-2 py-0.5 text-[10.5px] font-semibold ring-1 ring-inset" :class="assetHealth[selectedAsset.health].pill">
                  {{ assetHealth[selectedAsset.health].label }}
                </span>
                <span class="rounded px-1.5 py-0.5 font-mono text-[10.5px] font-bold" :style="{ background: assetKinds[selectedAsset.kind].accent + '1A', color: assetKinds[selectedAsset.kind].accent }">
                  {{ assetKinds[selectedAsset.kind].label }}
                </span>
                <span class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">{{ selectedAsset.group }}</span>
              </div>
            </div>
            <dl class="space-y-1.5 text-[11.5px]">
              <div class="flex justify-between gap-2"><dt class="text-slate-500">负责人</dt><dd class="font-mono text-slate-700">{{ selectedAsset.owner }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">最近物化</dt><dd class="font-mono text-slate-700">{{ selectedAsset.last_materialized || '—' }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">运行 ID</dt><dd class="font-mono text-slate-500">{{ selectedAsset.last_run_id ? selectedAsset.last_run_id.slice(0, 13) : '—' }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">行数</dt><dd class="font-mono text-slate-700">{{ selectedAsset.rows ? selectedAsset.rows.toLocaleString() : '—' }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">耗时</dt><dd class="font-mono text-slate-700">{{ selectedAsset.duration }}</dd></div>
              <div class="flex justify-between gap-2"><dt class="text-slate-500">上游依赖</dt><dd class="font-mono text-slate-700">{{ selectedAsset.upstream.length }} 个</dd></div>
            </dl>
            <div v-if="selectedAsset.partitions" class="border-t border-slate-200 pt-2.5">
              <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">分区状态（近 30 天）</p>
              <div class="flex items-center gap-2 text-[11px]">
                <span class="text-emerald-600 font-mono">●{{ selectedAsset.partitions.fresh }} 新鲜</span>
                <span class="text-amber-600 font-mono">●{{ selectedAsset.partitions.stale }} 过期</span>
                <span class="text-rose-600 font-mono">●{{ selectedAsset.partitions.failed }} 失败</span>
              </div>
            </div>
            <div class="flex flex-wrap gap-1.5 border-t border-slate-200 pt-2.5">
              <button class="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-blue-700">▶ 立即物化</button>
              <button class="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-semibold text-slate-700 hover:border-slate-300 hover:bg-slate-50">回填...</button>
              <button class="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-semibold text-slate-700 hover:border-slate-300 hover:bg-slate-50" @click="emit('open-run', selectedAsset.last_run_id)">查看运行</button>
            </div>
          </div>
        </div>

        <div class="flex min-h-0 flex-1 flex-col rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div class="border-b border-slate-200 px-3 py-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">监听器 / 调度</div>
          <ul class="flex-1 overflow-auto">
            <li v-for="(s, idx) in sensorActivity.recent_ticks" :key="idx" class="flex items-center gap-2 border-b border-slate-100 px-3 py-2 last:border-0 text-[11.5px]">
              <span class="font-mono text-[10px] text-slate-400">{{ s.type }}</span>
              <span class="truncate font-mono text-slate-700">{{ s.name }}</span>
              <span class="ml-auto font-mono text-slate-500">{{ s.last_tick }}</span>
              <span class="rounded-full px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset"
                    :class="s.status === '触发' ? 'bg-blue-50 text-blue-700 ring-blue-200' : 'bg-slate-50 text-slate-500 ring-slate-200'"
              >{{ s.status }}</span>
            </li>
          </ul>
        </div>
      </aside>
    </div>
  </div>
</template>
