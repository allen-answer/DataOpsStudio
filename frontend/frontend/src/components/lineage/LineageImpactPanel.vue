<script setup>
import { computed, ref } from 'vue'
import { GitBranch, ArrowRight } from 'lucide-vue-next'
import LineageFilterBar from './LineageFilterBar.vue'

const props = defineProps({
  impact: { type: Object, default: () => ({ downstream: {} }) },
})

const allEntries = computed(() => {
  const ds = props.impact?.downstream || {}
  return Object.entries(ds)
    .map(([source, list]) => ({ source, downstream: list || [] }))
    .filter(e => e.downstream.length)
    .sort((a, b) => b.downstream.length - a.downstream.length)
})

// ---------------- 筛选 ----------------
const search = ref('')
const minDepth = ref('all') // 影响下游表数：1 / 5 / 10 / all

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return allEntries.value.filter(e => {
    if (kw) {
      const hay = `${e.source} ${e.downstream.join(' ')}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (minDepth.value !== 'all') {
      const min = parseInt(minDepth.value, 10) || 0
      if (e.downstream.length < min) return false
    }
    return true
  })
})

const isFilterActive = computed(() => !!search.value || minDepth.value !== 'all')

function resetFilters() {
  search.value = ''
  minDepth.value = 'all'
}
</script>

<template>
  <section class="card space-y-3">
    <div class="flex items-center justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-800">影响分析</h3>
        <p class="muted text-xs">每张源表的传递闭包下游（任意一张源表改了，会波及哪些下游）</p>
      </div>
    </div>

    <LineageFilterBar
      v-if="allEntries.length"
      v-model:search="search"
      search-placeholder="源表 / 下游表搜索"
      :total="allEntries.length"
      :visible="filtered.length"
      :active="isFilterActive"
      @clear="resetFilters"
    >
      <template #filters>
        <select v-model="minDepth" class="filter-select">
          <option value="all">全部源表</option>
          <option value="2">下游 ≥ 2</option>
          <option value="5">下游 ≥ 5</option>
          <option value="10">下游 ≥ 10</option>
        </select>
      </template>
    </LineageFilterBar>

    <div v-if="!allEntries.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <GitBranch class="mx-auto mb-2 h-8 w-8 text-slate-300" />
      <p class="text-sm">无可分析的下游链路</p>
      <p class="muted text-xs">需要 INSERT 显式列出列名（SELECT * 暂不参与列级 lineage）</p>
    </div>

    <div v-else-if="!filtered.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <p class="text-sm">没有命中的影响链路</p>
      <p class="muted text-xs">调整搜索词或筛选条件，或点击"清空筛选"恢复</p>
    </div>

    <ul v-else class="space-y-2">
      <li
        v-for="entry in filtered" :key="entry.source"
        class="rounded-lg border border-slate-200 bg-slate-50 p-3"
      >
        <div class="flex flex-wrap items-center gap-2">
          <span class="sql-font text-sm font-semibold text-slate-800">{{ entry.source }}</span>
          <ArrowRight class="h-3.5 w-3.5 text-slate-300" />
          <span class="status-badge status-info">{{ entry.downstream.length }} 张下游</span>
        </div>
        <div class="mt-2 flex flex-wrap gap-1">
          <span
            v-for="t in entry.downstream" :key="t"
            class="rounded bg-white px-2 py-0.5 text-xs sql-font text-slate-700 shadow-sm"
          >{{ t }}</span>
        </div>
      </li>
    </ul>
  </section>
</template>
