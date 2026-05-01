<script setup>
import { ref } from 'vue'
import AssetGraphView from './dagster/AssetGraphView.vue'
import AssetDetailView from './dagster/AssetDetailView.vue'
import AssetRunView from './dagster/AssetRunView.vue'

const activePage = ref('graph')   // graph / asset / run
const focalAssetKey = ref('')
const focalRunId = ref('')

const goAsset = (key) => { focalAssetKey.value = key; activePage.value = 'asset' }
const goRun = (runId) => { focalRunId.value = runId; activePage.value = 'run' }
const goGraph = () => { activePage.value = 'graph' }

const subnav = [
  { id: 'graph',  label: '资产图谱', hint: '全局血缘视图' },
  { id: 'asset',  label: '资产详情', hint: 'analytics/daily_revenue' },
  { id: 'run',    label: '运行事件', hint: '结构化事件流' },
]
</script>

<template>
  <section class="space-y-3">
    <nav class="flex items-center gap-1 rounded-2xl border border-slate-200 bg-white p-1 shadow-sm">
      <button
        v-for="page in subnav" :key="page.id"
        class="flex flex-col items-start rounded-xl px-3 py-1.5 text-left transition"
        :class="activePage === page.id ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-50'"
        @click="activePage = page.id"
      >
        <span class="text-[12.5px] font-semibold">{{ page.label }}</span>
        <span class="text-[10.5px]" :class="activePage === page.id ? 'text-blue-100' : 'text-slate-400'">{{ page.hint }}</span>
      </button>
      <div class="ml-auto flex items-center gap-2 px-3 text-[10.5px] text-slate-500">
        <span>资产视角 · 借鉴 dagster.io 的资产中心化思路</span>
      </div>
    </nav>

    <AssetGraphView v-if="activePage === 'graph'" @open-asset="goAsset" @open-run="goRun" />
    <AssetDetailView v-else-if="activePage === 'asset'" @back="goGraph" @open-run="goRun" />
    <AssetRunView v-else-if="activePage === 'run'" @back="goGraph" />
  </section>
</template>
