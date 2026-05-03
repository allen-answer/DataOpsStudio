<script setup>
import { computed, ref, watch } from 'vue'
import { Workflow, FileText } from 'lucide-vue-next'
import LineageFilterBar from './LineageFilterBar.vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  preset: { type: Object, default: null },
})

const DML_PILL = {
  INSERT: 'bg-status-success-bg text-status-success',
  UPDATE: 'bg-status-info-bg text-status-info',
  MERGE: 'bg-status-info-bg text-status-info',
  DELETE: 'bg-status-error-bg text-status-error',
  TRUNCATE: 'bg-status-error-bg text-status-error',
  SELECT: 'bg-slate-100 text-slate-600',
  WITH: 'bg-slate-100 text-slate-600',
  REPLACE: 'bg-status-success-bg text-status-success',
  CREATE: 'bg-status-running-bg text-status-running',
}

const PARSE_LABEL = {
  parsed: '已解析',
  unsupported: '未支持',
  unknown: '—',
}
const PARSE_PILL = {
  parsed: 'status-success',
  unsupported: 'status-warning',
  unknown: 'status-pending',
}

// ---------------- 筛选 ----------------
const search = ref('')
const fileFilter = ref('all')
const procFilter = ref('all')
const dmlFilter = ref('all')
const parseFilter = ref('all')

// 文件名只显示 basename，完整路径塞 tooltip
const basename = (path) => {
  if (!path) return ''
  const i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return i >= 0 ? path.slice(i + 1) : path
}

const fileOptions = computed(() => {
  const s = new Set()
  for (const step of props.steps) if (step.file_name) s.add(step.file_name)
  return Array.from(s).sort()
})
const procOptions = computed(() => {
  const s = new Set()
  for (const step of props.steps) if (step.procedure_name) s.add(step.procedure_name)
  return Array.from(s).sort()
})
const dmlOptions = computed(() => {
  const s = new Set()
  for (const step of props.steps) if (step.dml_keyword) s.add(step.dml_keyword)
  return Array.from(s).sort()
})
const parseOptions = computed(() => {
  const s = new Set()
  for (const step of props.steps) if (step.parse_status) s.add(step.parse_status)
  return Array.from(s).sort()
})

const filteredSteps = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return props.steps.filter(step => {
    if (kw) {
      const hay = `${step.preceding_comment || ''} ${step.procedure_name || ''} ${step.dml_keyword || ''}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (fileFilter.value !== 'all' && step.file_name !== fileFilter.value) return false
    if (procFilter.value !== 'all' && step.procedure_name !== procFilter.value) return false
    if (dmlFilter.value !== 'all' && step.dml_keyword !== dmlFilter.value) return false
    if (parseFilter.value !== 'all' && step.parse_status !== parseFilter.value) return false
    return true
  })
})

// 按 procedure_name + file_name 分组
const grouped = computed(() => {
  const map = new Map()
  for (const step of filteredSteps.value) {
    const key = (step.file_name || '') + '|' + (step.procedure_name || '<top-level>')
    if (!map.has(key)) {
      map.set(key, {
        file_name: step.file_name || '',
        procedure_name: step.procedure_name || '',
        kind: step.kind || '',
        steps: [],
      })
    }
    map.get(key).steps.push(step)
  }
  return Array.from(map.values())
})

const isFilterActive = computed(() =>
  !!search.value
  || fileFilter.value !== 'all'
  || procFilter.value !== 'all'
  || dmlFilter.value !== 'all'
  || parseFilter.value !== 'all'
)

function resetFilters() {
  search.value = ''
  fileFilter.value = 'all'
  procFilter.value = 'all'
  dmlFilter.value = 'all'
  parseFilter.value = 'all'
}

watch(
  () => props.preset,
  (val) => {
    if (!val) return
    if (val.search != null) search.value = val.search
    if (val.fileFilter != null) fileFilter.value = val.fileFilter
    if (val.procFilter != null) procFilter.value = val.procFilter
    if (val.dmlFilter != null) dmlFilter.value = val.dmlFilter
    if (val.parseFilter != null) parseFilter.value = val.parseFilter
  },
  { immediate: true },
)
</script>

<template>
  <section class="card space-y-3">
    <div class="flex items-center justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-800">处理过程</h3>
        <p class="muted text-xs">{{ steps.length }} 段 DML（按存储过程 / 文件折叠）</p>
      </div>
    </div>

    <LineageFilterBar
      v-if="steps.length"
      v-model:search="search"
      search-placeholder="业务标题 / 过程名搜索"
      :total="steps.length"
      :visible="filteredSteps.length"
      :active="isFilterActive"
      @clear="resetFilters"
    >
      <template #filters>
        <select v-if="fileOptions.length > 1" v-model="fileFilter" class="filter-select" :title="fileFilter === 'all' ? '' : fileFilter">
          <option value="all">全部脚本</option>
          <option v-for="f in fileOptions" :key="f" :value="f">{{ basename(f) }}</option>
        </select>
        <select v-if="procOptions.length > 1" v-model="procFilter" class="filter-select">
          <option value="all">全部存储过程</option>
          <option v-for="p in procOptions" :key="p" :value="p">{{ p }}</option>
        </select>
        <select v-if="dmlOptions.length > 1" v-model="dmlFilter" class="filter-select">
          <option value="all">全部 DML</option>
          <option v-for="d in dmlOptions" :key="d" :value="d">{{ d }}</option>
        </select>
        <select v-if="parseOptions.length > 1" v-model="parseFilter" class="filter-select">
          <option value="all">全部解析状态</option>
          <option v-for="p in parseOptions" :key="p" :value="p">{{ PARSE_LABEL[p] || p }}</option>
        </select>
      </template>
    </LineageFilterBar>

    <div v-if="!steps.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <Workflow class="mx-auto mb-2 h-8 w-8 text-slate-300" />
      <p class="text-sm">没有抽取到处理步骤</p>
      <p class="muted text-xs">单个 INSERT/UPDATE 语句不计入；包/过程内的 DML 才会聚合</p>
    </div>

    <div v-else-if="!filteredSteps.length" class="rounded-lg border border-dashed border-slate-200 py-8 text-center text-slate-400">
      <p class="text-sm">没有命中的处理步骤</p>
      <p class="muted text-xs">调整筛选条件，或点击"清空筛选"恢复</p>
    </div>

    <div v-else class="space-y-3">
      <details
        v-for="g in grouped" :key="(g.file_name || '') + '|' + (g.procedure_name || '')"
        class="rounded-lg border border-slate-200 bg-slate-50"
        open
      >
        <summary class="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm">
          <FileText class="h-3.5 w-3.5 text-slate-400" />
          <span v-if="g.file_name" class="sql-font text-xs text-slate-500" :title="g.file_name">{{ basename(g.file_name) }}</span>
          <span v-if="g.file_name && g.procedure_name" class="text-slate-300">/</span>
          <span class="sql-font font-medium text-slate-800">{{ g.procedure_name || '顶层语句' }}</span>
          <span v-if="g.kind" class="muted text-[11px]">{{ g.kind }}</span>
          <span class="status-badge status-info ml-auto">{{ g.steps.length }} 段</span>
        </summary>
        <div class="border-t border-slate-200 bg-white px-3 py-2">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-left text-[10px] font-bold uppercase tracking-wider text-slate-400">
                <th class="py-1 pr-3">行号</th>
                <th class="py-1 pr-3">操作</th>
                <th class="py-1 pr-3">业务标题</th>
                <th class="py-1">解析</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="step in g.steps" :key="step.segment_index" class="border-t border-slate-100">
                <td class="py-1 pr-3 sql-font text-slate-500">
                  {{ step.line_start ?? '—' }}<span v-if="step.line_end && step.line_end !== step.line_start">–{{ step.line_end }}</span>
                </td>
                <td class="py-1 pr-3">
                  <span
                    v-if="step.dml_keyword"
                    class="rounded px-1.5 py-0.5 text-[10px] font-bold"
                    :class="DML_PILL[step.dml_keyword] || 'bg-slate-100 text-slate-700'"
                  >{{ step.dml_keyword }}</span>
                </td>
                <td class="py-1 pr-3 break-words text-slate-700">{{ step.preceding_comment || '—' }}</td>
                <td class="py-1">
                  <span class="status-badge" :class="PARSE_PILL[step.parse_status] || 'status-pending'">
                    {{ PARSE_LABEL[step.parse_status] || step.parse_status }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </div>
  </section>
</template>
