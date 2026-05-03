<script setup>
import { computed, ref, watch } from 'vue'
import LineageFilterBar from './lineage/LineageFilterBar.vue'

const props = defineProps({
  columns: { type: Array, default: () => [] },
  insertMappings: { type: Array, default: () => [] },
  preset: { type: Object, default: null },
})

// ---------------- 筛选：上半部分（columns）----------------
const colSearch = ref('')
const colConfidence = ref('all')
const colOnlyLowConfidence = ref(false)
const colOnlyAmbiguous = ref(false)

const colConfidences = computed(() => {
  const s = new Set()
  for (const c of props.columns) s.add(c.confidence || 'high')
  return Array.from(s).sort()
})

const filteredColumns = computed(() => {
  const kw = colSearch.value.trim().toLowerCase()
  return props.columns.filter(c => {
    if (kw) {
      const hay = `${c.output_column || ''} ${(c.source_columns || []).join(' ')} ${(c.source_tables || []).join(' ')}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (colConfidence.value !== 'all' && (c.confidence || 'high') !== colConfidence.value) return false
    if (colOnlyLowConfidence.value && (c.confidence || 'high') === 'high') return false
    if (colOnlyAmbiguous.value && !(c.warnings && c.warnings.length)) return false
    return true
  })
})

const colFilterActive = computed(
  () => !!colSearch.value
    || colConfidence.value !== 'all'
    || colOnlyLowConfidence.value
    || colOnlyAmbiguous.value
)
const resetColFilters = () => {
  colSearch.value = ''
  colConfidence.value = 'all'
  colOnlyLowConfidence.value = false
  colOnlyAmbiguous.value = false
}

// ---------------- 筛选：下半部分（insertMappings）----------------
const mapSearch = ref('')
const mapConfidence = ref('all')
const mapTargetTable = ref('all')
const mapOnlyLowConfidence = ref(false)
const mapOnlyAmbiguous = ref(false)

const mapConfidences = computed(() => {
  const s = new Set()
  for (const c of props.insertMappings) s.add(c.confidence || 'high')
  return Array.from(s).sort()
})
const mapTargetTables = computed(() => {
  const s = new Set()
  for (const c of props.insertMappings) if (c.target_table) s.add(c.target_table)
  return Array.from(s).sort()
})

const filteredMappings = computed(() => {
  const kw = mapSearch.value.trim().toLowerCase()
  return props.insertMappings.filter(c => {
    if (kw) {
      const hay = `${c.target_table || ''} ${c.target_column || ''} ${(c.source_columns || []).join(' ')} ${(c.source_tables || []).join(' ')}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (mapConfidence.value !== 'all' && (c.confidence || 'high') !== mapConfidence.value) return false
    if (mapTargetTable.value !== 'all' && c.target_table !== mapTargetTable.value) return false
    if (mapOnlyLowConfidence.value && (c.confidence || 'high') === 'high') return false
    if (mapOnlyAmbiguous.value && !(c.warnings && c.warnings.length)) return false
    return true
  })
})

const mapFilterActive = computed(
  () => !!mapSearch.value
    || mapConfidence.value !== 'all'
    || mapTargetTable.value !== 'all'
    || mapOnlyLowConfidence.value
    || mapOnlyAmbiguous.value
)
const resetMapFilters = () => {
  mapSearch.value = ''
  mapConfidence.value = 'all'
  mapTargetTable.value = 'all'
  mapOnlyLowConfidence.value = false
  mapOnlyAmbiguous.value = false
}

watch(
  () => props.preset,
  (val) => {
    if (!val) return
    if (val.colSearch != null) colSearch.value = val.colSearch
    if (val.colConfidence != null) colConfidence.value = val.colConfidence
    if (val.colOnlyLowConfidence != null) colOnlyLowConfidence.value = val.colOnlyLowConfidence
    if (val.colOnlyAmbiguous != null) colOnlyAmbiguous.value = val.colOnlyAmbiguous
    if (val.mapSearch != null) mapSearch.value = val.mapSearch
    if (val.mapConfidence != null) mapConfidence.value = val.mapConfidence
    if (val.mapTargetTable != null) mapTargetTable.value = val.mapTargetTable
    if (val.mapOnlyLowConfidence != null) mapOnlyLowConfidence.value = val.mapOnlyLowConfidence
    if (val.mapOnlyAmbiguous != null) mapOnlyAmbiguous.value = val.mapOnlyAmbiguous
  },
  { immediate: true },
)
</script>

<template>
  <div class="space-y-4">
    <!-- 顶层 SELECT 字段血缘 -->
    <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-lg font-bold text-slate-800">顶层 SELECT 字段血缘</h2>
      <LineageFilterBar
        v-if="columns.length"
        v-model:search="colSearch"
        search-placeholder="字段名 / 来源表搜索"
        :total="columns.length"
        :visible="filteredColumns.length"
        :active="colFilterActive"
        @clear="resetColFilters"
        class="mb-3"
      >
        <template #filters>
          <select v-if="colConfidences.length > 1" v-model="colConfidence" class="filter-select">
            <option value="all">全部可信度</option>
            <option v-for="c in colConfidences" :key="c" :value="c">{{ c }}</option>
          </select>
          <label class="inline-flex items-center gap-1 text-[11px] text-slate-600">
            <input v-model="colOnlyLowConfidence" type="checkbox" class="h-3.5 w-3.5">
            仅低可信度
          </label>
          <label class="inline-flex items-center gap-1 text-[11px] text-slate-600" title="warnings 非空 —— 来源表或字段无法静态确定">
            <input v-model="colOnlyAmbiguous" type="checkbox" class="h-3.5 w-3.5">
            仅歧义字段
          </label>
        </template>
      </LineageFilterBar>
      <div v-if="!columns.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
        <p class="text-sm">无字段血缘数据</p>
      </div>
      <div v-else-if="!filteredColumns.length" class="rounded-lg border border-dashed border-slate-200 py-6 text-center text-slate-400">
        <p class="text-sm">没有命中的字段</p>
      </div>
      <div v-else class="overflow-auto">
        <table>
          <thead><tr><th>输出字段</th><th>来源字段</th><th>来源表</th><th>可信度</th><th>变量</th><th>表达式</th></tr></thead>
          <tbody>
            <tr v-for="item in filteredColumns" :key="item.output_column + item.expression">
              <td>{{ item.output_column }}</td>
              <td>{{ item.source_columns.join(', ') }}</td>
              <td>{{ item.source_tables.join(', ') }}</td>
              <td>{{ item.confidence || 'high' }}</td>
              <td>{{ item.variables.join(', ') }}</td>
              <td>
                <code>{{ item.expression }}</code>
                <p v-if="item.warnings?.length" class="mt-1 text-xs text-amber-600">{{ item.warnings.map(w => w.type).join(', ') }}</p>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 落表字段映射 -->
    <div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-lg font-bold text-slate-800">落表字段映射</h2>
      <LineageFilterBar
        v-if="insertMappings.length"
        v-model:search="mapSearch"
        search-placeholder="目标字段 / 来源字段 / 来源表搜索"
        :total="insertMappings.length"
        :visible="filteredMappings.length"
        :active="mapFilterActive"
        @clear="resetMapFilters"
        class="mb-3"
      >
        <template #filters>
          <select v-if="mapTargetTables.length > 1" v-model="mapTargetTable" class="filter-select">
            <option value="all">全部目标表</option>
            <option v-for="t in mapTargetTables" :key="t" :value="t">{{ t }}</option>
          </select>
          <select v-if="mapConfidences.length > 1" v-model="mapConfidence" class="filter-select">
            <option value="all">全部可信度</option>
            <option v-for="c in mapConfidences" :key="c" :value="c">{{ c }}</option>
          </select>
          <label class="inline-flex items-center gap-1 text-[11px] text-slate-600">
            <input v-model="mapOnlyLowConfidence" type="checkbox" class="h-3.5 w-3.5">
            仅低可信度
          </label>
          <label class="inline-flex items-center gap-1 text-[11px] text-slate-600" title="warnings 非空 —— 来源未识别 / 转换不确定">
            <input v-model="mapOnlyAmbiguous" type="checkbox" class="h-3.5 w-3.5">
            仅歧义字段
          </label>
        </template>
      </LineageFilterBar>
      <div v-if="!insertMappings.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
        <p class="text-sm">无落表字段映射</p>
      </div>
      <div v-else-if="!filteredMappings.length" class="rounded-lg border border-dashed border-slate-200 py-6 text-center text-slate-400">
        <p class="text-sm">没有命中的字段映射</p>
      </div>
      <div v-else class="overflow-auto">
        <table>
          <thead><tr><th>目标表</th><th>目标字段</th><th>来源字段</th><th>来源表</th><th>可信度</th><th>处理逻辑</th></tr></thead>
          <tbody>
            <tr v-for="item in filteredMappings" :key="item.target_table + item.target_column + item.position">
              <td>{{ item.target_table }}</td>
              <td>{{ item.target_column }}</td>
              <td>{{ item.source_columns.join(', ') }}</td>
              <td>{{ item.source_tables.join(', ') }}</td>
              <td>{{ item.confidence || 'high' }}</td>
              <td>
                {{ item.transform }}
                <p v-if="item.warnings?.length" class="mt-1 text-xs text-amber-600">{{ item.warnings.map(w => w.type).join(', ') }}</p>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
