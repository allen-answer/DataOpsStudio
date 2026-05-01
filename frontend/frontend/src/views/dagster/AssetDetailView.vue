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
  success: { label: '成功', dot: 'bg-emerald-500', pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
  failed:  { label: '失败', dot: 'bg-rose-500',    pill: 'bg-rose-50 text-rose-700 ring-rose-200' },
}

const partitionDot = (status) => {
  if (status === 'ok') return 'bg-emerald-500'
  if (status === 'stale') return 'bg-amber-500'
  if (status === 'failed') return 'bg-rose-500'
  return 'bg-slate-200'
}
const partitionLabel = (status) => ({ ok: '正常', stale: '过期', failed: '失败', missing: '缺失' }[status] || status)

const checkSeverityClass = (sev) => sev === '阻断'
  ? 'text-rose-700 bg-rose-50 ring-rose-200'
  : 'text-amber-700 bg-amber-50 ring-amber-200'

const tabs = [
  { id: 'overview',   label: '总览' },
  { id: 'mats',       label: '物化历史', count: materializations.length },
  { id: 'checks',     label: '资产检查', count: assetChecks.length },
  { id: 'lineage',    label: '上下游' },
  { id: 'definition', label: '定义' },
]

const codeLines = computed(() => focalDefinition.split('\n'))
</script>

<template>
  <div class="flex h-[calc(100vh-200px)] flex-col gap-3">
    <!-- header -->
    <div class="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div class="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-[11px] text-slate-500">
            <button class="text-slate-600 transition hover:text-blue-600" @click="emit('back')">← 资产图谱</button>
            <span class="text-slate-300">/</span>
            <span class="font-mono">{{ focal.group }}</span>
          </div>
          <div class="mt-1 flex items-center gap-2.5">
            <span class="h-2.5 w-2.5 rounded-full" :class="assetHealth[focal.health].dot"></span>
            <h1 class="font-mono text-xl font-bold text-slate-800">{{ focal.key }}</h1>
            <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="assetHealth[focal.health].pill">
              {{ assetHealth[focal.health].label }}
            </span>
            <span class="rounded px-1.5 py-0.5 font-mono text-[11px] font-bold" :style="{ background: assetKinds[focal.kind].accent + '1A', color: assetKinds[focal.kind].accent }">
              {{ assetKinds[focal.kind].label }}
            </span>
          </div>
          <p class="mt-1.5 max-w-2xl text-[12.5px] text-slate-500">面向财务 / 高管 dashboard 的日度收入汇总。将清洗后的订单与汇率 join，按（日期、币种、业务线）输出每行一条记录。</p>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white transition hover:bg-blue-700">
            <span>▶</span> 立即物化
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">
            回填...
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50" @click="emit('open-run', focal.last_run_id)">
            最近运行 →
          </button>
          <button class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50">⋯</button>
        </div>
      </div>

      <!-- tabs -->
      <nav class="flex border-b border-slate-200 px-2 text-[12px]">
        <button
          v-for="t in tabs" :key="t.id"
          class="relative flex items-center gap-1.5 border-b-2 px-3 py-2 font-semibold transition"
          :class="tab === t.id ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'"
          @click="tab = t.id"
        >
          {{ t.label }}
          <span v-if="t.count !== undefined" class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-mono text-slate-500">{{ t.count }}</span>
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
          <div class="rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
              <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">分区热力（近 30 天）</span>
              <span class="font-mono text-[10.5px] text-slate-500">最近 = {{ partitionStrip[partitionStrip.length - 1].partition }}</span>
            </div>
            <div class="p-3">
              <div class="grid gap-1" style="grid-template-columns: repeat(30, minmax(0, 1fr));">
                <div
                  v-for="cell in partitionStrip" :key="cell.partition"
                  class="aspect-square rounded-sm transition hover:scale-110"
                  :class="partitionDot(cell.status)"
                  :title="`${cell.partition} — ${partitionLabel(cell.status)}`"
                ></div>
              </div>
              <div class="mt-3 flex items-center gap-3 text-[10.5px] text-slate-500">
                <span class="flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-emerald-500"></span>正常</span>
                <span class="flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-amber-500"></span>过期</span>
                <span class="flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-rose-500"></span>失败</span>
                <span class="flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-slate-200"></span>缺失</span>
              </div>
            </div>
          </div>

          <!-- materialization timeline -->
          <div class="rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
              <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">最近物化记录</span>
              <button class="text-[10.5px] font-semibold text-blue-600 hover:text-blue-700" @click="tab = 'mats'">查看全部 →</button>
            </div>
            <ul class="divide-y divide-slate-100">
              <li v-for="mat in materializations.slice(0, 4)" :key="mat.id" class="flex items-center gap-3 px-3 py-2 text-[12px]">
                <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="matStatusMeta[mat.status].dot"></span>
                <span class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset" :class="matStatusMeta[mat.status].pill">{{ matStatusMeta[mat.status].label }}</span>
                <span class="shrink-0 font-mono text-slate-600">{{ mat.started_at.slice(5) }}</span>
                <span class="shrink-0 font-mono text-slate-500">分区 {{ mat.partition }}</span>
                <span class="shrink-0 font-mono text-slate-500">{{ mat.duration }}</span>
                <button class="ml-auto truncate font-mono text-blue-600 hover:text-blue-700" @click="emit('open-run', mat.run_id)">{{ mat.run_id.slice(0, 13) }} →</button>
              </li>
            </ul>
          </div>

          <!-- checks summary -->
          <div class="rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
              <span class="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">资产检查</span>
              <button class="text-[10.5px] font-semibold text-blue-600 hover:text-blue-700" @click="tab = 'checks'">查看全部 →</button>
            </div>
            <ul class="divide-y divide-slate-100">
              <li v-for="check in assetChecks" :key="check.name" class="flex items-center gap-3 px-3 py-2 text-[12px]">
                <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="check.status === 'passed' ? 'bg-emerald-500' : 'bg-rose-500'"></span>
                <span class="shrink-0 font-mono font-semibold text-slate-700">{{ check.name }}</span>
                <span class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="checkSeverityClass(check.severity)">{{ check.severity }}</span>
                <span class="truncate text-slate-600">{{ check.message }}</span>
                <span class="ml-auto shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="check.status === 'passed' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : 'bg-rose-50 text-rose-700 ring-rose-200'">{{ check.status === 'passed' ? '通过' : '未通过' }}</span>
              </li>
            </ul>
          </div>
        </div>

        <!-- MATERIALIZATIONS -->
        <div v-else-if="tab === 'mats'" class="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table class="w-full text-[12px]">
            <thead class="border-b border-slate-200 bg-slate-50">
              <tr class="text-left">
                <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">状态</th>
                <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">开始时间</th>
                <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">分区</th>
                <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">耗时</th>
                <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">操作人</th>
                <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">元数据</th>
                <th class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">运行</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="mat in materializations" :key="mat.id" class="border-b border-slate-100 last:border-0">
                <td class="px-3 py-2.5"><span class="inline-flex items-center gap-1.5 rounded-full px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset" :class="matStatusMeta[mat.status].pill"><span class="h-1.5 w-1.5 rounded-full" :class="matStatusMeta[mat.status].dot"></span>{{ matStatusMeta[mat.status].label }}</span></td>
                <td class="px-3 py-2.5 font-mono text-slate-700">{{ mat.started_at }}</td>
                <td class="px-3 py-2.5 font-mono text-slate-500">{{ mat.partition }}</td>
                <td class="px-3 py-2.5 font-mono text-slate-500">{{ mat.duration }}</td>
                <td class="px-3 py-2.5 font-mono text-slate-500">{{ mat.operator }}</td>
                <td class="px-3 py-2.5">
                  <div class="flex flex-wrap gap-1">
                    <span v-for="(value, key) in mat.metadata" :key="key" class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px]">
                      <span class="text-slate-500">{{ key }}=</span><span class="text-slate-700">{{ value }}</span>
                    </span>
                  </div>
                </td>
                <td class="px-3 py-2.5">
                  <button class="font-mono text-blue-600 hover:text-blue-700" @click="emit('open-run', mat.run_id)">{{ mat.run_id.slice(0, 13) }} →</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- CHECKS -->
        <div v-else-if="tab === 'checks'" class="space-y-2">
          <div v-for="check in assetChecks" :key="check.name" class="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
            <div class="flex items-center gap-2.5">
              <span class="h-2 w-2 rounded-full" :class="check.status === 'passed' ? 'bg-emerald-500' : 'bg-rose-500'"></span>
              <span class="font-mono text-[13.5px] font-semibold text-slate-800">{{ check.name }}</span>
              <span class="rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ring-inset" :class="checkSeverityClass(check.severity)">{{ check.severity }}</span>
              <span class="ml-auto rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset" :class="check.status === 'passed' ? 'bg-emerald-50 text-emerald-700 ring-emerald-200' : 'bg-rose-50 text-rose-700 ring-rose-200'">{{ check.status === 'passed' ? '通过' : '未通过' }}</span>
            </div>
            <p class="mt-1.5 text-[12px] text-slate-600">{{ check.message }}</p>
            <p class="mt-1 font-mono text-[10.5px] text-slate-400">最近评估：{{ check.last_run }}</p>
          </div>
        </div>

        <!-- LINEAGE -->
        <div v-else-if="tab === 'lineage'" class="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div class="grid grid-cols-3 gap-4">
            <div>
              <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">上游 ({{ upstreamAssets.length }})</p>
              <div class="space-y-1.5">
                <div v-for="up in upstreamAssets" :key="up.key" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2">
                  <span class="h-1.5 w-1.5 rounded-full" :class="assetHealth[up.health].dot"></span>
                  <span class="truncate font-mono text-[12px] text-slate-700">{{ up.key }}</span>
                  <span class="ml-auto rounded px-1 font-mono text-[9.5px] font-bold" :style="{ background: assetKinds[up.kind].accent + '1A', color: assetKinds[up.kind].accent }">{{ assetKinds[up.kind].glyph }}</span>
                </div>
              </div>
            </div>
            <div class="flex flex-col items-center justify-center">
              <div class="text-center">
                <p class="text-[10.5px] uppercase tracking-wider text-slate-400">焦点</p>
                <div class="mt-2 rounded-xl border-2 border-blue-400 bg-blue-50 px-3 py-3">
                  <div class="flex items-center gap-2">
                    <span class="h-2 w-2 rounded-full" :class="assetHealth[focal.health].dot"></span>
                    <span class="font-mono text-[13px] font-semibold text-slate-800">{{ focal.key }}</span>
                  </div>
                </div>
              </div>
            </div>
            <div>
              <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">下游 ({{ downstreamAssets.length }})</p>
              <div class="space-y-1.5">
                <div v-for="down in downstreamAssets" :key="down.key" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2">
                  <span class="h-1.5 w-1.5 rounded-full" :class="assetHealth[down.health].dot"></span>
                  <span class="truncate font-mono text-[12px] text-slate-700">{{ down.key }}</span>
                  <span class="ml-auto rounded px-1 font-mono text-[9.5px] font-bold" :style="{ background: assetKinds[down.kind].accent + '1A', color: assetKinds[down.kind].accent }">{{ assetKinds[down.kind].glyph }}</span>
                </div>
                <p v-if="!downstreamAssets.length" class="rounded-lg border border-dashed border-slate-200 px-3 py-2 text-center text-[11px] text-slate-400">没有直接下游</p>
              </div>
            </div>
          </div>
        </div>

        <!-- DEFINITION -->
        <div v-else-if="tab === 'definition'" class="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div class="flex items-center justify-between border-b border-slate-200 px-3 py-2">
            <span class="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">analytics_repo / assets / daily_revenue.py</span>
            <button class="text-[10.5px] font-semibold text-blue-600 hover:text-blue-700">在仓库中打开 →</button>
          </div>
          <pre class="overflow-x-auto bg-slate-950 px-3 py-3 font-mono text-[12px] leading-relaxed"><span v-for="(line, idx) in codeLines" :key="idx" class="flex"><span class="mr-3 w-8 shrink-0 select-none text-right text-slate-600">{{ idx + 1 }}</span><span class="flex-1 whitespace-pre text-slate-200">{{ line }}</span><br/></span></pre>
        </div>
      </div>

      <!-- side panel -->
      <aside class="flex min-h-0 flex-col gap-3 overflow-hidden">
        <div class="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">属性</p>
          <dl class="space-y-1.5 text-[12px]">
            <div class="flex justify-between gap-2"><dt class="text-slate-500">分组</dt><dd class="font-mono text-slate-700">{{ focal.group }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">类型</dt><dd class="font-mono text-slate-700">{{ assetKinds[focal.kind].label }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">负责人</dt><dd class="font-mono text-slate-700">{{ focal.owner }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">最近物化</dt><dd class="font-mono text-slate-700">{{ focal.last_materialized }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">耗时</dt><dd class="font-mono text-slate-700">{{ focal.duration }}</dd></div>
            <div class="flex justify-between gap-2"><dt class="text-slate-500">行数</dt><dd class="font-mono text-slate-700">{{ focal.rows ? focal.rows.toLocaleString() : '—' }}</dd></div>
          </dl>
          <div class="mt-3 border-t border-slate-200 pt-2.5">
            <p class="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">标签</p>
            <div class="flex flex-wrap gap-1">
              <span class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">tier=tier-1</span>
              <span class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">env=prod</span>
              <span class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-600">team=analytics</span>
            </div>
          </div>
        </div>

        <div class="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">新鲜度策略</p>
          <p class="text-[12px] text-slate-700">应在上游变更后 <span class="font-mono font-semibold text-blue-600">2 小时</span> 内完成物化。</p>
          <p class="mt-1.5 text-[11px] text-amber-700">⚠ 当前已超出 SLA 6 小时 12 分钟</p>
        </div>

        <div class="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
          <p class="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">自动物化策略</p>
          <p class="text-[12px] text-slate-700">策略：<span class="font-mono font-semibold text-blue-600">eager（即时）</span></p>
          <p class="mt-1 text-[11px] text-slate-500">任一上游有新数据时即触发物化。</p>
        </div>
      </aside>
    </div>
  </div>
</template>
