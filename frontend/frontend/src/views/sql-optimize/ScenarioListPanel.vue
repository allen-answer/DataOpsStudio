<script setup lang="ts">
// scenario 列表左栏(Phase 14 P2 拆出)
import { useScenarioLabStore } from '../../stores/scenarioLab'

const store = useScenarioLabStore()
</script>

<template>
  <aside class="space-y-3">
    <div class="text-xs uppercase tracking-wider text-slate-500 font-bold">
      可用模板（{{ store.validScenarios.length }}）
    </div>
    <div v-if="store.loadingList" class="muted text-sm">加载中…</div>
    <div v-else-if="!store.validScenarios.length" class="card p-4 text-sm text-slate-500">
      <p>config/scenarios/ 下无可用 yml。</p>
      <p class="mt-2 text-xs">把 example 复制成 `*.yml` 即可上架(或用顶部「从 datasource 导入」反向生成)。</p>
    </div>
    <button
      v-for="it in store.validScenarios"
      :key="it.path"
      class="w-full text-left card p-4 transition-all hover:border-primary hover:shadow-md"
      :class="store.isSelected(it.id) ? 'border-primary shadow-md ring-2 ring-primary/20' : ''"
      @click="store.selectScenario(it.id || '')"
    >
      <div class="flex items-start justify-between gap-2">
        <div class="font-medium text-slate-800">{{ it.name || it.id }}</div>
        <span v-if="it.dialect" class="pill bg-slate-100 text-slate-600">{{ it.dialect }}</span>
      </div>
      <div class="mt-1 text-xs text-slate-500 sql-font">{{ it.id }}</div>
      <div v-if="it.tags?.length" class="mt-2 flex flex-wrap gap-1">
        <span v-for="t in it.tags" :key="t" class="pill bg-primary-light text-primary">{{ t }}</span>
      </div>
    </button>
  </aside>
</template>
