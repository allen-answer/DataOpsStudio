<script setup>
import { computed, ref, watch } from 'vue'
import { ShieldCheck, AlertTriangle, AlertCircle } from 'lucide-vue-next'
import LineageFilterBar from './LineageFilterBar.vue'

const props = defineProps({
  risks: { type: Array, default: () => [] },
  // 上游 LineageReportView 透传：从总览卡片跳过来时把 levelFilter / typeFilter
  // 等预设到这里。watch 进内部 ref 再照常工作，用户后续可手动修改。
  preset: { type: Object, default: null },
})

const RISK_TONE = {
  high:   { card: 'border-status-error-bg bg-status-error-bg/40', text: 'text-status-error', icon: AlertCircle },
  medium: { card: 'border-status-warning-bg bg-status-warning-bg/40', text: 'text-status-warning', icon: AlertTriangle },
  low:    { card: 'border-slate-200 bg-slate-50', text: 'text-slate-600', icon: AlertTriangle },
}

// ---------------- 筛选 ----------------
const search = ref('')
const levelFilter = ref('all')
const typeFilter = ref('all')
const fileFilter = ref('all')

const basename = (path) => {
  if (!path) return ''
  const i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return i >= 0 ? path.slice(i + 1) : path
}

const levelOptions = computed(() => {
  const s = new Set()
  for (const r of props.risks) if (r.level) s.add(r.level)
  // high → medium → low 排序
  const order = { high: 0, medium: 1, low: 2 }
  return Array.from(s).sort((a, b) => (order[a] ?? 9) - (order[b] ?? 9))
})
const typeOptions = computed(() => {
  const s = new Set()
  for (const r of props.risks) if (r.type) s.add(r.type)
  return Array.from(s).sort()
})
const fileOptions = computed(() => {
  const s = new Set()
  for (const r of props.risks) if (r.file_name) s.add(r.file_name)
  return Array.from(s).sort()
})

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return props.risks.filter(r => {
    if (kw) {
      const hay = `${r.message || ''} ${r.type || ''} ${r.file_name || ''}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (levelFilter.value !== 'all' && r.level !== levelFilter.value) return false
    if (typeFilter.value !== 'all' && r.type !== typeFilter.value) return false
    if (fileFilter.value !== 'all' && r.file_name !== fileFilter.value) return false
    return true
  })
})

const isFilterActive = computed(() =>
  !!search.value
  || levelFilter.value !== 'all'
  || typeFilter.value !== 'all'
  || fileFilter.value !== 'all'
)

function resetFilters() {
  search.value = ''
  levelFilter.value = 'all'
  typeFilter.value = 'all'
  fileFilter.value = 'all'
}

// 接收外部预设（一次性）：mount 时和 preset 变化时都跑一次
watch(
  () => props.preset,
  (val) => {
    if (!val) return
    if (val.search != null) search.value = val.search
    if (val.levelFilter != null) levelFilter.value = val.levelFilter
    if (val.typeFilter != null) typeFilter.value = val.typeFilter
    if (val.fileFilter != null) fileFilter.value = val.fileFilter
  },
  { immediate: true },
)
</script>

<template>
  <section class="card space-y-3">
    <div class="flex items-center justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-800">风险与告警</h3>
        <p class="muted text-xs">{{ risks.length }} 条 · 按 high / medium / low 分级</p>
      </div>
    </div>

    <LineageFilterBar
      v-if="risks.length"
      v-model:search="search"
      search-placeholder="风险描述 / 类型搜索"
      :total="risks.length"
      :visible="filtered.length"
      :active="isFilterActive"
      @clear="resetFilters"
    >
      <template #filters>
        <select v-if="levelOptions.length > 1" v-model="levelFilter" class="filter-select">
          <option value="all">全部等级</option>
          <option v-for="l in levelOptions" :key="l" :value="l">{{ l }}</option>
        </select>
        <select v-if="typeOptions.length > 1" v-model="typeFilter" class="filter-select">
          <option value="all">全部类型</option>
          <option v-for="t in typeOptions" :key="t" :value="t">{{ t }}</option>
        </select>
        <select v-if="fileOptions.length > 1" v-model="fileFilter" class="filter-select" :title="fileFilter === 'all' ? '' : fileFilter">
          <option value="all">全部脚本</option>
          <option v-for="f in fileOptions" :key="f" :value="f">{{ basename(f) }}</option>
        </select>
      </template>
    </LineageFilterBar>

    <div v-if="!risks.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center">
      <ShieldCheck class="mx-auto mb-2 h-8 w-8 text-status-success" />
      <p class="text-sm font-medium text-slate-700">未检测到风险</p>
      <p class="muted text-xs">所有解析路径均成功，无低置信动态 SQL</p>
    </div>

    <div v-else-if="!filtered.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <p class="text-sm">没有命中的风险</p>
      <p class="muted text-xs">调整筛选条件，或点击"清空筛选"恢复</p>
    </div>

    <ul v-else class="space-y-2">
      <li
        v-for="(risk, i) in filtered" :key="i"
        class="rounded-lg border p-3 text-sm"
        :class="RISK_TONE[risk.level]?.card || RISK_TONE.low.card"
      >
        <div class="flex items-start gap-2">
          <component :is="RISK_TONE[risk.level]?.icon || AlertTriangle" class="mt-0.5 h-4 w-4 shrink-0" :class="RISK_TONE[risk.level]?.text || RISK_TONE.low.text" />
          <div class="flex-1 min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span
                class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-widest"
                :class="risk.level === 'high' ? 'bg-status-error text-white'
                       : risk.level === 'medium' ? 'bg-status-warning text-white'
                       : 'bg-slate-200 text-slate-700'"
              >{{ risk.level }}</span>
              <span class="font-medium" :class="RISK_TONE[risk.level]?.text || RISK_TONE.low.text">{{ risk.type }}</span>
              <span v-if="risk.file_name" class="muted sql-font text-[11px]" :title="risk.file_name">{{ basename(risk.file_name) }}</span>
            </div>
            <p class="mt-1 break-words text-slate-700">{{ risk.message }}</p>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
