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
  { id: 'graph',  label: 'Asset Graph',  hint: 'Global lineage' },
  { id: 'asset',  label: 'Asset Detail', hint: 'analytics/daily_revenue' },
  { id: 'run',    label: 'Run Events',   hint: 'Structured event stream' },
]
</script>

<template>
  <!-- Dark canvas wraps the whole prototype so the surrounding shell still
       sees normal body background but this view feels like Dagster. -->
  <section class="-mx-8 -my-8 min-h-[calc(100vh-64px)] bg-slate-950 px-8 py-6 text-slate-100">
    <nav class="mb-4 flex items-center gap-1 rounded border border-slate-800 bg-slate-900 p-1">
      <button
        v-for="page in subnav" :key="page.id"
        class="flex flex-col items-start rounded px-3 py-1.5 text-left transition"
        :class="activePage === page.id ? 'bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500/40' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'"
        @click="activePage = page.id"
      >
        <span class="text-[12.5px] font-semibold">{{ page.label }}</span>
        <span class="text-[10.5px]" :class="activePage === page.id ? 'text-cyan-400/80' : 'text-slate-500'">{{ page.hint }}</span>
      </button>
      <div class="ml-auto flex items-center gap-2 px-3 text-[10.5px] text-slate-500">
        <span class="font-mono">prototype · asset-oriented · inspired by dagster.io</span>
      </div>
    </nav>

    <AssetGraphView v-if="activePage === 'graph'" @open-asset="goAsset" @open-run="goRun" />
    <AssetDetailView v-else-if="activePage === 'asset'" @back="goGraph" @open-run="goRun" />
    <AssetRunView v-else-if="activePage === 'run'" @back="goGraph" />
  </section>
</template>
